package umicollapse.main;

import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.ValidationStringency;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.SAMRecordIterator;
import htsjdk.samtools.SAMFileWriter;
import htsjdk.samtools.SAMFileWriterFactory;
import htsjdk.samtools.SAMFileHeader;

import java.util.Map;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.PriorityQueue;

import java.util.stream.Stream;

import java.io.File;

import umicollapse.util.BitSet;
import umicollapse.data.*;
import umicollapse.algo.*;
import umicollapse.merge.*;
import umicollapse.util.Read;
import umicollapse.util.SAMRead;
import umicollapse.util.ReadFreq;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Utils;
import static umicollapse.util.Utils.HASH_CONST;

public class DeduplicateSAM{
    private static final int MIN_ALIGN_MAP_CAPACITY = 1 << 16;
    private static final int MAX_ALIGN_MAP_CAPACITY = 1 << 24;
    // Positive-strand unclipped starts can precede coordinate sort starts by soft/hard clipping.
    // Keep a conservative window and fail if a flushed alignment key is seen again.
    private static final int STREAMING_POSITIVE_LAG = Integer.getInteger("umicollapse.streaming.positiveLag", 10000);
    private static final boolean STREAMING_VALIDATE_FLUSH = Boolean.parseBoolean(System.getProperty("umicollapse.streaming.validateFlush", "true"));
    private static final String STREAMING_MODE = System.getProperty("umicollapse.streaming.mode", "auto").toLowerCase();

    private int avgUMICount;
    private int maxUMICount;
    private int dedupedCount;
    private int umiLength;

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        SAMRead.setDefaultUMIPattern(umiSeparator);

        SamReader reader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);

        if(canUseStreamingSingleEnd(reader, algo, dataClass, parallel, paired, trackClusters)){
            deduplicateAndMergeSingleEndStreaming(in, out, reader, algo, dataClass, merge, umiLengthParam, k, percentage, keepUnmapped);
            return;
        }

        Writer writer = new Writer(in, out, reader, paired);
        Map<Alignment, Map<BitSet, ReadFreq>> align = new HashMap<>(estimatedAlignmentMapCapacity(in));

        umiLength = umiLengthParam;
        int totalReadCount = 0;
        int unmapped = 0;
        int unpaired = 0;
        int chimeric = 0;
        int readCount = 0;

        for(SAMRecord record : reader){
            // always skip the reversed read
            if(paired && record.getReadPairedFlag() && record.getSecondOfPairFlag())
                continue;

            totalReadCount++;

            if(record.getReadUnmappedFlag()){ // discard unmapped reads
                unmapped++;
                if(keepUnmapped)
                    writer.write(record);
                continue;
            }

            if(paired){
                if(!record.getReadPairedFlag()){
                    unpaired++;

                    if(removeUnpaired)
                        continue;
                }

                if(record.getReadPairedFlag() && record.getMateUnmappedFlag()){
                    unmapped++;
                    continue;
                }

                if(record.getReadPairedFlag() && !record.getReferenceName().equals(record.getMateReferenceName())){
                    chimeric++;

                    if(removeChimeric)
                        continue;
                }
            }

            Alignment alignment = null;

            if(paired){
                alignment = new PairedAlignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName(),
                        record.getInferredInsertSize()
                );
            }else{
                alignment = new Alignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName()
                );
            }

            Map<BitSet, ReadFreq> umiRead = align.get(alignment);

            if(umiRead == null){
                umiRead = new HashMap<BitSet, ReadFreq>(4);
                align.put(alignment, umiRead);
            }

            Read read = new SAMRead(record);
            BitSet umi = read.getUMI(umiLength);

            if(umiLength == -1)
                umiLength = read.getUMILength();

            ReadFreq prev = umiRead.get(umi);

            if(prev != null){
                prev.read = merge.merge(read, prev.read);
                prev.freq++;
            }else{
                umiRead.put(umi, new ReadFreq(read, 1));
            }

            readCount++;
        }

        try{
            reader.close();
        }catch(Exception e){
            e.printStackTrace();
        }

        reader = null;

        System.gc(); // attempt to clear up memory before deduplicating

        System.out.println("Done reading input file into memory!");

        int alignPosCount = align.size();
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;
        Object lock = new Object();

        final Map<Alignment, ClusterTracker> clusterTrackers = trackClusters ? new HashMap<Alignment, ClusterTracker>() : null;

        Stream<Map.Entry<Alignment, Map<BitSet, ReadFreq>>> stream =
            parallel ? align.entrySet().parallelStream() : ((paired && !trackClusters) ? align.entrySet().stream().sorted((a, b) -> a.getKey().getRef().compareTo(b.getKey().getRef())) : align.entrySet().stream());

        stream.forEach(e -> {
            List<Read> deduped;
            Data data = null;

            try{
                data = dataClass.getDeclaredConstructor().newInstance();
            }catch(Exception ex){
                ex.printStackTrace();
            }

            ClusterTracker currTracker = new ClusterTracker(trackClusters);

            if(algo instanceof Algorithm)
                deduped = ((Algorithm)algo).apply(e.getValue(), (DataStructure)data, currTracker, umiLength, k, percentage);
            else
                deduped = ((ParallelAlgorithm)algo).apply(e.getValue(), (ParallelDataStructure)data, currTracker, umiLength, k, percentage);

            synchronized(lock){
                currTracker.setOffset(dedupedCount);

                avgUMICount += e.getValue().size();
                maxUMICount = Math.max(maxUMICount, e.getValue().size());
                dedupedCount += deduped.size();

                if(trackClusters){
                    clusterTrackers.put(e.getKey(), currTracker);
                }else{
                    for(Read read : deduped)
                        writer.write(((SAMRead)read).toSAMRecord());
                }
            }
        });

        // second pass to tag reads with their cluster and other stats
        if(trackClusters){
            System.gc(); // attempt to clear up memory before second pass

            System.out.println("Done with the first pass for tracking clusters!");

            SamReader reader2 = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);

            for(SAMRecord record : reader2){
                if(record.getReadUnmappedFlag()) // discard unmapped reads
                    continue;

                if(paired && ((removeUnpaired && !record.getReadPairedFlag()) // discard unpaired
                            || (record.getReadPairedFlag() && record.getSecondOfPairFlag()) // ignore reversed reads
                            || (record.getReadPairedFlag() && record.getMateUnmappedFlag()) // discard unmapped reads
                            || (removeChimeric && record.getReadPairedFlag()
                                && !record.getReferenceName().equals(record.getMateReferenceName())))){ // discard chimeric reads
                    continue;
                }

                Alignment alignment = null;

                if(paired){
                    alignment = new PairedAlignment(
                            record.getReadNegativeStrandFlag(),
                            record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                            record.getReferenceName(),
                            record.getInferredInsertSize()
                    );
                }else{
                    alignment = new Alignment(
                            record.getReadNegativeStrandFlag(),
                            record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                            record.getReferenceName()
                    );
                }

                ClusterTracker currTracker = clusterTrackers.get(alignment);
                Map<BitSet, ReadFreq> map = align.get(alignment);

                Read read = new SAMRead(record);
                BitSet umi = read.getUMI(umiLength);

                int id = currTracker.getId(umi);
                ClusterTracker.ClusterStats stats = currTracker.getStats(id);
                int absId = id + currTracker.getOffset();
                SAMRecord record2 = record.deepCopy();
                ReadFreq readFreq = map.get(umi);

                record2.setAttribute("MI", absId + "");
                record2.setAttribute("RX", Utils.toString(stats.getUMI(), umiLength));

                if(stats.getUMI().equals(umi) && stats.getRead().equals(read)){
                    record2.setAttribute("cs", stats.getFreq());
                    record2.setAttribute("su", readFreq.freq);
                }else{
                    record2.setDuplicateReadFlag(true);

                    if(readFreq.read.equals(read))
                        record2.setAttribute("su", readFreq.freq);
                }

                writer.write(record2);
            }

            try{
                reader2.close();
            }catch(Exception e){
                e.printStackTrace();
            }
        }

        writer.close();

        System.out.println("Number of input reads\t" + totalReadCount);
        System.out.println("Number of removed unmapped reads\t" + unmapped);

        if(paired){
            System.out.println("Number of unpaired reads\t" + unpaired);
            System.out.println("Number of chimeric reads\t" + chimeric);
        }

        System.out.println("Number of unremoved reads\t" + readCount);
        System.out.println("Number of unique alignment positions\t" + alignPosCount);
        System.out.println("Average number of UMIs per alignment position\t" + ((double)avgUMICount / alignPosCount));
        System.out.println("Max number of UMIs over all alignment positions\t" + maxUMICount);

        if(trackClusters)
            System.out.println("Number of groups of reads\t" + dedupedCount);
        else
            System.out.println("Number of reads after deduplicating\t" + dedupedCount);
    }

    private boolean canUseStreamingSingleEnd(SamReader reader, Algo algo, Class<? extends Data> dataClass, boolean parallel, boolean paired, boolean trackClusters){
        boolean eligible = !paired
            && !parallel
            && !trackClusters
            && algo instanceof Algorithm
            && DataStructure.class.isAssignableFrom(dataClass)
            && reader.getFileHeader().getSortOrder() == SAMFileHeader.SortOrder.coordinate;

        if(STREAMING_MODE.equals("off"))
            return false;

        if(STREAMING_MODE.equals("auto"))
            return eligible;

        if(STREAMING_MODE.equals("on")){
            if(!eligible)
                throw new UnsupportedOperationException("Streaming mode requires coordinate-sorted, single-end, non-parallel SAM/BAM without cluster tracking");

            return true;
        }

        throw new IllegalArgumentException("Invalid streaming mode '" + STREAMING_MODE + "'; expected auto, on, or off");
    }

    private void deduplicateAndMergeSingleEndStreaming(File in, File out, SamReader reader, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean keepUnmapped){
        Writer writer = new Writer(in, out, reader, false);
        Map<Alignment, StreamingAlignReads> active = new HashMap<>(MIN_ALIGN_MAP_CAPACITY);
        PriorityQueue<StreamingAlignReads> ready = new PriorityQueue<>((a, b) -> Integer.compare(a.flushStart, b.flushStart));
        HashSet<Alignment> flushed = STREAMING_VALIDATE_FLUSH ? new HashSet<Alignment>(MIN_ALIGN_MAP_CAPACITY) : null;

        umiLength = umiLengthParam;
        int totalReadCount = 0;
        int unmapped = 0;
        int readCount = 0;
        int alignPosCount = 0;
        String currentRef = null;

        System.out.println("Using coordinate-sorted single-end streaming fast path");

        for(SAMRecord record : reader){
            totalReadCount++;

            if(record.getReadUnmappedFlag()){
                unmapped++;
                if(keepUnmapped)
                    writer.write(record);
                continue;
            }

            String recordRef = record.getReferenceName();

            if(currentRef == null){
                currentRef = recordRef;
            }else if(!currentRef.equals(recordRef)){
                flushAllStreamingGroups(active, ready, writer, algo, dataClass, k, percentage);
                if(flushed != null)
                    flushed.clear();
                currentRef = recordRef;
            }

            flushReadyStreamingGroups(active, ready, flushed, writer, algo, dataClass, k, percentage, record.getAlignmentStart());

            Alignment alignment = singleEndAlignment(record);

            if(flushed != null && flushed.contains(alignment))
                throw new IllegalStateException("Streaming positive-lag window was too small for alignment " + alignment.getRef() + ":" + alignment.coord + " strand=" + alignment.strand + "; rerun with -Dumicollapse.streaming.positiveLag=<larger value>");

            StreamingAlignReads alignReads = active.get(alignment);

            if(alignReads == null){
                alignReads = new StreamingAlignReads(alignment, streamingFlushStart(alignment));
                active.put(alignment, alignReads);
                ready.add(alignReads);
                alignPosCount++;
            }

            addRead(alignReads.umiRead, record, merge);
            readCount++;
        }

        flushAllStreamingGroups(active, ready, writer, algo, dataClass, k, percentage);

        try{
            reader.close();
        }catch(Exception e){
            e.printStackTrace();
        }

        writer.close();

        System.out.println("Number of input reads\t" + totalReadCount);
        System.out.println("Number of removed unmapped reads\t" + unmapped);
        System.out.println("Number of unremoved reads\t" + readCount);
        System.out.println("Number of unique alignment positions\t" + alignPosCount);
        System.out.println("Average number of UMIs per alignment position\t" + ((double)avgUMICount / alignPosCount));
        System.out.println("Max number of UMIs over all alignment positions\t" + maxUMICount);
        System.out.println("Number of reads after deduplicating\t" + dedupedCount);
    }

    private void flushReadyStreamingGroups(Map<Alignment, StreamingAlignReads> active, PriorityQueue<StreamingAlignReads> ready, HashSet<Alignment> flushed, Writer writer, Algo algo, Class<? extends Data> dataClass, int k, float percentage, int currentStart){
        while(!ready.isEmpty() && ready.peek().flushStart < currentStart){
            StreamingAlignReads alignReads = ready.poll();

            if(active.remove(alignReads.alignment) == null)
                continue;

            flushStreamingGroup(alignReads, writer, algo, dataClass, k, percentage);

            if(flushed != null)
                flushed.add(alignReads.alignment);
        }
    }

    private void flushAllStreamingGroups(Map<Alignment, StreamingAlignReads> active, PriorityQueue<StreamingAlignReads> ready, Writer writer, Algo algo, Class<? extends Data> dataClass, int k, float percentage){
        while(!ready.isEmpty()){
            StreamingAlignReads alignReads = ready.poll();

            if(active.remove(alignReads.alignment) != null)
                flushStreamingGroup(alignReads, writer, algo, dataClass, k, percentage);
        }
    }

    private void flushStreamingGroup(StreamingAlignReads alignReads, Writer writer, Algo algo, Class<? extends Data> dataClass, int k, float percentage){
        List<Read> deduped;
        Data data = null;

        try{
            data = dataClass.getDeclaredConstructor().newInstance();
        }catch(Exception ex){
            ex.printStackTrace();
        }

        deduped = ((Algorithm)algo).apply(alignReads.umiRead, (DataStructure)data, new ClusterTracker(false), umiLength, k, percentage);

        avgUMICount += alignReads.umiRead.size();
        maxUMICount = Math.max(maxUMICount, alignReads.umiRead.size());
        dedupedCount += deduped.size();

        for(Read read : deduped)
            writer.write(((SAMRead)read).toSAMRecord());
    }

    private void addRead(Map<BitSet, ReadFreq> umiRead, SAMRecord record, Merge merge){
        Read read = new SAMRead(record);
        BitSet umi = read.getUMI(umiLength);

        if(umiLength == -1)
            umiLength = read.getUMILength();

        ReadFreq prev = umiRead.get(umi);

        if(prev != null){
            prev.read = merge.merge(read, prev.read);
            prev.freq++;
        }else{
            umiRead.put(umi, new ReadFreq(read, 1));
        }
    }

    private static Alignment singleEndAlignment(SAMRecord record){
        return new Alignment(
                record.getReadNegativeStrandFlag(),
                record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                record.getReferenceName()
        );
    }

    private static int streamingFlushStart(Alignment alignment){
        if(alignment.strand)
            return alignment.coord;

        long flushStart = (long)alignment.coord + STREAMING_POSITIVE_LAG;
        return flushStart > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int)flushStart;
    }

    private static int estimatedAlignmentMapCapacity(File in){
        long estimated = in.length() / 128L;

        if(estimated < MIN_ALIGN_MAP_CAPACITY)
            return MIN_ALIGN_MAP_CAPACITY;

        if(estimated > MAX_ALIGN_MAP_CAPACITY)
            return MAX_ALIGN_MAP_CAPACITY;

        return (int)estimated;
    }

    // trade off speed for lower memory usage
    // input should be sorted based on alignment for best results
    public void deduplicateAndMergeTwoPass(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        SamReader firstPass = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
        Writer writer = new Writer(in, out, firstPass, paired);
        Map<Alignment, AlignReads> align = new HashMap<>(1 << 16);
        int totalReadCount = 0;
        int unmapped = 0;
        int unpaired = 0;
        int chimeric = 0;
        int readCount = 0;

        // first pass to figure out where each alignment position ends
        for(SAMRecord record : firstPass){
            // always skip the reversed read
            if(paired && record.getReadPairedFlag() && record.getSecondOfPairFlag())
                continue;

            totalReadCount++;

            if(record.getReadUnmappedFlag()){ // discard unmapped reads
                unmapped++;
                if(keepUnmapped)
                    writer.write(record);
                continue;
            }

            if(paired){
                if(!record.getReadPairedFlag()){
                    unpaired++;

                    if(removeUnpaired)
                        continue;
                }

                if(record.getReadPairedFlag() && record.getMateUnmappedFlag()){
                    unmapped++;
                    continue;
                }

                if(record.getReadPairedFlag() && !record.getReferenceName().equals(record.getMateReferenceName())){
                    chimeric++;

                    if(removeChimeric)
                        continue;
                }
            }

            Alignment alignment = null;

            if(paired){
                alignment = new PairedAlignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName(),
                        record.getInferredInsertSize()
                );
            }else{
                alignment = new Alignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName()
                );
            }

            if(!align.containsKey(alignment))
                align.put(alignment, new AlignReads());

            align.get(alignment).latest = readCount;
            readCount++;
        }

        try{
            firstPass.close();
        }catch(Exception e){
            e.printStackTrace();
        }

        firstPass = null;

        System.gc(); // attempt to clear up memory before second pass

        System.out.println("Done with the first pass!");

        SAMRead.setDefaultUMIPattern(umiSeparator);

        SamReader reader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);

        umiLength = umiLengthParam;
        int idx = 0;
        int alignPosCount = align.size();
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;

        for(SAMRecord record : reader){
            if(record.getReadUnmappedFlag()) // discard unmapped reads
                continue;

            if(paired && ((removeUnpaired && !record.getReadPairedFlag()) // discard unpaired
                        || (record.getReadPairedFlag() && record.getSecondOfPairFlag()) // ignore reversed reads
                        || (record.getReadPairedFlag() && record.getMateUnmappedFlag()) // discard unmapped reads
                        || (removeChimeric && record.getReadPairedFlag()
                            && !record.getReferenceName().equals(record.getMateReferenceName())))){ // discard chimeric reads
                continue;
            }

            Alignment alignment = null;

            if(paired){
                alignment = new PairedAlignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName(),
                        record.getInferredInsertSize()
                );
            }else{
                alignment = new Alignment(
                        record.getReadNegativeStrandFlag(),
                        record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                        record.getReferenceName()
                );
            }

            AlignReads alignReads = align.get(alignment);

            if(alignReads.umiRead == null)
                alignReads.umiRead = new HashMap<BitSet, ReadFreq>(4);

            Read read = new SAMRead(record);
            BitSet umi = read.getUMI(umiLength);

            if(umiLength == -1)
                umiLength = read.getUMILength();

            if(alignReads.umiRead.containsKey(umi)){
                ReadFreq prev = alignReads.umiRead.get(umi);
                prev.read = merge.merge(read, prev.read);
                prev.freq++;
            }else{
                alignReads.umiRead.put(umi, new ReadFreq(read, 1));
            }

            if(idx >= alignReads.latest){
                List<Read> deduped;
                Data data = null;

                try{
                    data = dataClass.getDeclaredConstructor().newInstance();
                }catch(Exception ex){
                    ex.printStackTrace();
                }

                if(algo instanceof Algorithm)
                    deduped = ((Algorithm)algo).apply(alignReads.umiRead, (DataStructure)data, new ClusterTracker(trackClusters), umiLength, k, percentage);
                else
                    deduped = ((ParallelAlgorithm)algo).apply(alignReads.umiRead, (ParallelDataStructure)data, new ClusterTracker(trackClusters), umiLength, k, percentage);

                avgUMICount += alignReads.umiRead.size();
                maxUMICount = Math.max(maxUMICount, alignReads.umiRead.size());
                dedupedCount += deduped.size();

                for(Read r : deduped)
                    writer.write(((SAMRead)r).toSAMRecord());

                // done with the current alignment position, so free up memory
                align.remove(alignment);
            }

            idx++;
        }

        try{
            reader.close();
        }catch(Exception e){
            e.printStackTrace();
        }

        writer.close();

        System.out.println("Number of input reads\t" + totalReadCount);
        System.out.println("Number of removed unmapped reads\t" + unmapped);

        if(paired){
            System.out.println("Number of unpaired reads\t" + unpaired);
            System.out.println("Number of chimeric reads\t" + chimeric);
        }

        System.out.println("Number of unremoved reads\t" + readCount);
        System.out.println("Number of unique alignment positions\t" + alignPosCount);
        System.out.println("Average number of UMIs per alignment position\t" + ((double)avgUMICount / alignPosCount));
        System.out.println("Max number of UMIs over all alignment positions\t" + maxUMICount);
        System.out.println("Number of reads after deduplicating\t" + dedupedCount);
    }

    private static class ReversedRead implements Comparable{
        private String name, ref;
        private int coord;

        public ReversedRead(String name, String ref, int coord){
            this.name = name;
            this.ref = ref.intern();
            this.coord = coord;
        }

        @Override
        public boolean equals(Object o){
            if(!(o instanceof ReversedRead))
                return false;

            ReversedRead a = (ReversedRead)o;

            if(this == a)
                return true;

            if(ref != a.ref)
                return false;

            if(!name.equals(a.name))
                return false;

            return true;
        }

        @Override
        public int hashCode(){
            int hash = name.hashCode();
            hash = hash * HASH_CONST + ref.hashCode();
            hash = hash * HASH_CONST + coord;
            return hash;
        }

        @Override
        public int compareTo(Object o){
            ReversedRead other = (ReversedRead)o;

            if(coord != other.coord)
                return coord - other.coord;

            if(ref != other.ref)
                return ref.compareTo(other.ref);

            return name.compareTo(other.name);
        }
    }

    // heavily inspired by TwoPassPairWriter from UMI-tools
    private static class Writer{
        private boolean paired;
        private SAMFileWriter writer;
        private SamReader reader;

        private String ref = null;
        private HashSet<ReversedRead> set;

        public Writer(File in, File out, SamReader r, boolean paired){
            if(paired){
                this.reader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
                this.set = new HashSet<ReversedRead>();
            }

            SAMFileHeader header = r.getFileHeader().clone();
            header.setSortOrder(SAMFileHeader.SortOrder.unsorted);
            this.writer = new SAMFileWriterFactory().makeSAMOrBAMWriter(header, true, out);
            this.paired = paired;
        }

        public void write(SAMRecord record){
            if(paired){ // must be forwards read
                String currRef = record.getReferenceName();

                if(ref == null)
                    ref = currRef;

                if(!ref.equals(currRef)){
                    writeReversed(false);
                    ref = currRef;
                }

                if(record.getReadPairedFlag()){
                    set.add(new ReversedRead(
                            record.getReadName(),
                            record.getMateReferenceName(),
                            record.getMateAlignmentStart()
                    ));
                }
            }

            writer.addAlignment(record);
        }

        public void close(){
            if(paired) {
                writeReversed(true);
                try{
                    reader.close();
                }catch(Exception e){
                    e.printStackTrace();
                }
            }

            writer.close();
        }

        private void writeReversed(boolean fullPass){
            if(ref == null)
                return;

            SAMRecordIterator iter = null;

            if(fullPass)
                iter = reader.iterator();
            else
                iter = reader.query(ref, 0, 0, true);

            while(iter.hasNext()){
                SAMRecord record = iter.next();

                if(!record.getReadUnmappedFlag()
                        && record.getReadPairedFlag()
                        && record.getSecondOfPairFlag()
                        && !record.getMateUnmappedFlag()){
                    ReversedRead read = new ReversedRead(
                            record.getReadName(),
                            record.getReferenceName(),
                            record.getAlignmentStart()
                    );

                    if(set.contains(read)){
                        writer.addAlignment(record);
                        set.remove(read);
                    }
                }
            }

            iter.close();

        }
    }

    private static class AlignReads{
        public int latest;
        public Map<BitSet, ReadFreq> umiRead;

        public AlignReads(){
            this.latest = 0;
            this.umiRead = null;
        }
    }

    private static class StreamingAlignReads{
        public Alignment alignment;
        public int flushStart;
        public Map<BitSet, ReadFreq> umiRead;

        public StreamingAlignReads(Alignment alignment, int flushStart){
            this.alignment = alignment;
            this.flushStart = flushStart;
            this.umiRead = new HashMap<BitSet, ReadFreq>(4);
        }
    }

    private static class PairedAlignment extends Alignment{
        private int tlen;

        public PairedAlignment(boolean strand, int coord, String ref, int tlen){
            super(strand, coord, ref);
            this.tlen = tlen;
        }

        @Override
        public boolean equals(Object o){
            if(!(o instanceof Alignment))
                return false;

            PairedAlignment a = (PairedAlignment)o;

            if(this == a)
                return true;

            if(tlen != a.tlen)
                return false;

            return super.equals(a);
        }

        @Override
        public int hashCode(){
            int hash = super.hashCode();
            hash = hash * HASH_CONST + tlen;
            return hash;
        }

        @Override
        public int compareTo(Object o){
            PairedAlignment other = (PairedAlignment)o;

            if(tlen != other.tlen)
                return Integer.compare(tlen, other.tlen);

            return super.compareTo(other);
        }
    }

    private static class Alignment implements Comparable{
        private boolean strand;
        private int coord;
        private String ref;

        public Alignment(boolean strand, int coord, String ref){
            this.strand = strand;
            this.coord = coord;
            this.ref = ref.intern();
        }

        public String getRef(){
            return ref;
        }

        @Override
        public boolean equals(Object o){
            if(!(o instanceof Alignment))
                return false;

            Alignment a = (Alignment)o;

            if(this == a)
                return true;

            if(strand != a.strand)
                return false;

            if(coord != a.coord)
                return false;

            if(ref != a.ref) // can directly compare interned strings
                return false;

            return true;
        }

        @Override
        public int hashCode(){
            int hash = strand ? 1231 : 1237;
            hash = hash * HASH_CONST + coord;
            hash = hash * HASH_CONST + ref.hashCode();
            return hash;
        }

        @Override
        public int compareTo(Object o){
            Alignment other = (Alignment)o;

            if(strand != other.strand)
                return Boolean.compare(strand, other.strand);

            if(coord != other.coord)
                return coord - other.coord;

            return ref.compareTo(other.ref);
        }
    }
}
