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
import java.util.Locale;
import java.util.PriorityQueue;

import java.util.stream.Stream;

import java.io.File;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

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
    // Positive-strand unclipped starts can precede coordinate sort starts by soft/hard clipping.
    // Keep a conservative window and reject any record whose leading clipping exceeds it.
    private static final int STREAMING_POSITIVE_LAG = Integer.getInteger("umicollapse.streaming.positiveLag", 10000);

    private int avgUMICount;
    private int maxUMICount;
    private int dedupedCount;
    private int umiLength;

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        deduplicateAndMerge(
                in, out, algo, dataClass, merge, umiLengthParam, k, percentage,
                parallel, umiSeparator, paired, removeUnpaired, removeChimeric,
                keepUnmapped, trackClusters,
                System.getProperty("umicollapse.streaming.mode", "auto")
        );
    }

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters, String streamingMode){
        SAMRead.setDefaultUMIPattern(umiSeparator);
        streamingMode = streamingMode.toLowerCase(Locale.ROOT);

        SamReader reader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
        boolean useStreaming;

        try{
            useStreaming = canUseStreamingSingleEnd(reader, algo, dataClass, parallel, paired, trackClusters, streamingMode);
        }catch(RuntimeException | Error ex){
            try{
                reader.close();
            }catch(Exception closeEx){
                ex.addSuppressed(closeEx);
            }
            throw ex;
        }

        if(useStreaming){
            try{
                deduplicateAndMergeSingleEndStreaming(in, out, reader, algo, dataClass, merge, umiLengthParam, k, percentage, keepUnmapped);
                return;
            }catch(StreamingFallbackException ex){
                if(!streamingMode.equals("auto"))
                    throw ex;

                System.err.println("Streaming fast path was not safe for this input; retrying with --streaming-mode off: " + ex.getMessage());
                reader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
            }
        }

        Writer writer = new Writer(in, out, reader, paired, false);
        Map<Alignment, Map<BitSet, ReadFreq>> align = new HashMap<>(MIN_ALIGN_MAP_CAPACITY);

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

    private boolean canUseStreamingSingleEnd(SamReader reader, Algo algo, Class<? extends Data> dataClass, boolean parallel, boolean paired, boolean trackClusters, String streamingMode){
        boolean eligible = !paired
            && !parallel
            && !trackClusters
            && algo instanceof Algorithm
            && DataStructure.class.isAssignableFrom(dataClass)
            && reader.getFileHeader().getSortOrder() == SAMFileHeader.SortOrder.coordinate;

        if(streamingMode.equals("off"))
            return false;

        if(streamingMode.equals("auto"))
            return eligible;

        if(streamingMode.equals("on")){
            if(!eligible){
                SAMFileHeader.SortOrder sortOrder = reader.getFileHeader().getSortOrder();
                throw new UnsupportedOperationException(
                        "Streaming mode requires @HD SO:coordinate input, single-end reads, non-parallel execution, "
                        + "and cluster tracking disabled; observed sortOrder=" + sortOrder
                        + ". Sort the input BAM with samtools sort before dUMI, or run with --streaming-mode off."
                );
            }

            return true;
        }

        throw new IllegalArgumentException("Invalid streaming mode '" + streamingMode + "'; expected auto, on, or off");
    }

    private void deduplicateAndMergeSingleEndStreaming(File in, File out, SamReader reader, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean keepUnmapped){
        File temporaryOut = null;
        Writer writer = null;
        boolean writerClosed = false;
        Map<Alignment, StreamingAlignReads> active = new HashMap<>(MIN_ALIGN_MAP_CAPACITY);
        PriorityQueue<StreamingAlignReads> ready = new PriorityQueue<>((a, b) -> Integer.compare(a.flushStart, b.flushStart));

        umiLength = umiLengthParam;
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;
        int totalReadCount = 0;
        int unmapped = 0;
        int readCount = 0;
        int alignPosCount = 0;
        String currentRef = null;
        int lastReferenceIndex = Integer.MIN_VALUE;
        int lastAlignmentStart = Integer.MIN_VALUE;

        try{
            temporaryOut = createStreamingTemporaryFile(out);
            writer = new Writer(in, temporaryOut, reader, false, true);
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
                int referenceIndex = record.getReferenceIndex();
                int alignmentStart = record.getAlignmentStart();

                if(referenceIndex < lastReferenceIndex || (referenceIndex == lastReferenceIndex && alignmentStart < lastAlignmentStart)){
                    throw new StreamingFallbackException(
                            "Streaming mode requires records to be in coordinate order, but read " + record.getReadName()
                            + " at " + recordRef + ":" + alignmentStart
                            + " follows referenceIndex=" + lastReferenceIndex + " start=" + lastAlignmentStart
                            + ". Sort the input BAM with samtools sort before dUMI, or run with --streaming-mode off."
                    );
                }

                lastReferenceIndex = referenceIndex;
                lastAlignmentStart = alignmentStart;

                if(currentRef == null){
                    currentRef = recordRef;
                }else if(!currentRef.equals(recordRef)){
                    flushAllStreamingGroups(active, ready, writer, algo, dataClass, k, percentage);
                    currentRef = recordRef;
                }

                Alignment alignment = singleEndAlignment(record);
                validateStreamingLag(record, alignment);
                flushReadyStreamingGroups(active, ready, writer, algo, dataClass, k, percentage, alignmentStart);

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

            writer.close();
            writerClosed = true;
            promoteStreamingOutput(temporaryOut, out);
        }catch(RuntimeException | Error ex){
            if(writer != null && !writerClosed){
                try{
                    writer.close();
                }catch(RuntimeException | Error closeEx){
                    ex.addSuppressed(closeEx);
                }
            }

            try{
                if(temporaryOut != null)
                    Files.deleteIfExists(temporaryOut.toPath());
            }catch(IOException deleteEx){
                ex.addSuppressed(deleteEx);
            }

            throw ex;
        }finally{
            try{
                reader.close();
            }catch(Exception e){
                e.printStackTrace();
            }
        }

        System.out.println("Number of input reads\t" + totalReadCount);
        System.out.println("Number of removed unmapped reads\t" + unmapped);
        System.out.println("Number of unremoved reads\t" + readCount);
        System.out.println("Number of unique alignment positions\t" + alignPosCount);
        System.out.println("Average number of UMIs per alignment position\t" + ((double)avgUMICount / alignPosCount));
        System.out.println("Max number of UMIs over all alignment positions\t" + maxUMICount);
        System.out.println("Number of reads after deduplicating\t" + dedupedCount);
    }

    private void flushReadyStreamingGroups(Map<Alignment, StreamingAlignReads> active, PriorityQueue<StreamingAlignReads> ready, Writer writer, Algo algo, Class<? extends Data> dataClass, int k, float percentage, int currentStart){
        while(!ready.isEmpty() && ready.peek().flushStart < currentStart){
            StreamingAlignReads alignReads = ready.poll();

            if(active.remove(alignReads.alignment) == null)
                continue;

            flushStreamingGroup(alignReads, writer, algo, dataClass, k, percentage);
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

    private static void validateStreamingLag(SAMRecord record, Alignment alignment){
        if(alignment.strand)
            return;

        long leadingClip = (long)record.getAlignmentStart() - alignment.coord;

        if(leadingClip > STREAMING_POSITIVE_LAG){
            throw new StreamingFallbackException(
                    "Streaming positive-lag window is too small for read " + record.getReadName()
                    + " with " + leadingClip + " leading clipped bases; rerun with "
                    + "-Dumicollapse.streaming.positiveLag=<at-least-" + leadingClip + ">"
                    + " or use --streaming-mode off."
            );
        }
    }

    private static int streamingFlushStart(Alignment alignment){
        if(alignment.strand)
            return alignment.coord;

        long flushStart = (long)alignment.coord + STREAMING_POSITIVE_LAG;
        return flushStart > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int)flushStart;
    }

    private static File createStreamingTemporaryFile(File out){
        File absoluteOut = out.getAbsoluteFile();
        File parent = absoluteOut.getParentFile();
        String name = absoluteOut.getName().toLowerCase(Locale.ROOT);
        String suffix = name.endsWith(".bam") ? ".bam" : ".sam";

        try{
            return File.createTempFile(".dumi-stream-", suffix, parent);
        }catch(IOException ex){
            throw new IllegalStateException("Could not create a temporary streaming output next to " + out, ex);
        }
    }

    private static void promoteStreamingOutput(File temporaryOut, File out){
        try{
            try{
                Files.move(temporaryOut.toPath(), out.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            }catch(AtomicMoveNotSupportedException ex){
                Files.move(temporaryOut.toPath(), out.toPath(), StandardCopyOption.REPLACE_EXISTING);
            }
        }catch(IOException ex){
            throw new IllegalStateException("Could not move completed streaming output to " + out, ex);
        }
    }

    // trade off speed for lower memory usage
    // input should be sorted based on alignment for best results
    public void deduplicateAndMergeTwoPass(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        SamReader firstPass = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
        Writer writer = new Writer(in, out, firstPass, paired, false);
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

        public Writer(File in, File out, SamReader r, boolean paired, boolean streaming){
            SamReader pairedReader = null;
            SAMFileWriter outputWriter = null;

            try{
                if(paired){
                    pairedReader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
                    this.set = new HashSet<ReversedRead>();
                }

                if(streaming){
                    SAMFileHeader header = r.getFileHeader().clone();
                    header.setSortOrder(SAMFileHeader.SortOrder.unsorted);
                    outputWriter = new SAMFileWriterFactory().makeSAMOrBAMWriter(header, true, out);
                }else{
                    outputWriter = new SAMFileWriterFactory().makeSAMOrBAMWriter(r.getFileHeader(), false, out);
                }
            }catch(RuntimeException | Error ex){
                if(outputWriter != null){
                    try{
                        outputWriter.close();
                    }catch(RuntimeException | Error closeEx){
                        ex.addSuppressed(closeEx);
                    }
                }

                if(pairedReader != null){
                    try{
                        pairedReader.close();
                    }catch(Exception closeEx){
                        ex.addSuppressed(closeEx);
                    }
                }

                throw ex;
            }

            this.reader = pairedReader;
            this.writer = outputWriter;
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
            Throwable failure = null;

            try{
                if(paired)
                    writeReversed(true);
            }catch(RuntimeException | Error ex){
                failure = ex;
            }

            if(paired){
                try{
                    reader.close();
                }catch(Exception ex){
                    failure = combineFailures(failure, ex);
                }
            }

            try{
                writer.close();
            }catch(RuntimeException | Error ex){
                failure = combineFailures(failure, ex);
            }

            if(failure instanceof RuntimeException)
                throw (RuntimeException)failure;
            if(failure instanceof Error)
                throw (Error)failure;
            if(failure != null)
                throw new IllegalStateException("Could not close alignment resources", failure);
        }

        private void writeReversed(boolean fullPass){
            if(ref == null)
                return;

            SAMRecordIterator iter = null;

            if(fullPass)
                iter = reader.iterator();
            else
                iter = reader.query(ref, 0, 0, true);

            try{
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
            }finally{
                iter.close();
            }
        }

        private static Throwable combineFailures(Throwable first, Throwable next){
            if(first == null)
                return next;

            first.addSuppressed(next);
            return first;
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

    private static class StreamingFallbackException extends IllegalStateException{
        public StreamingFallbackException(String message){
            super(message);
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
