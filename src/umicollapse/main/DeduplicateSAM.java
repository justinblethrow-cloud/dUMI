package umicollapse.main;

import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.ValidationStringency;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.SAMRecordIterator;
import htsjdk.samtools.SAMFileWriter;
import htsjdk.samtools.SAMFileWriterFactory;
import htsjdk.samtools.SAMFileHeader;
import htsjdk.samtools.CigarElement;
import htsjdk.samtools.CigarOperator;

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

    private long avgUMICount;
    private int maxUMICount;
    private long dedupedCount;
    private int umiLength;

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        deduplicateAndMerge(
                in, out, algo, dataClass, merge, umiLengthParam, k, percentage,
                parallel, umiSeparator, paired, removeUnpaired, removeChimeric,
                keepUnmapped, trackClusters,
                System.getProperty("umicollapse.streaming.mode", "off")
        );
    }

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters, String streamingMode){
        OutputTransaction.runAlignment(
                in,
                out,
                stagedOutput -> deduplicateAndMergeCore(
                        in, stagedOutput, algo, dataClass, merge, umiLengthParam, k,
                        percentage, parallel, umiSeparator, paired, removeUnpaired,
                        removeChimeric, keepUnmapped, trackClusters, streamingMode
                )
        );
    }

    void deduplicateAndMergeCore(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters, String streamingMode){
        validateKnownK(k, umiLengthParam);
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

        Map<Alignment, Map<BitSet, ReadFreq>> align = new HashMap<>(MIN_ALIGN_MAP_CAPACITY);

        umiLength = umiLengthParam;
        long totalReadCount = 0L;
        long unmapped = 0L;
        long unpaired = 0L;
        long chimeric = 0L;
        long readCount = 0L;
        int alignPosCount;
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;
        Object lock = new Object();

        final Map<Alignment, ClusterTracker> clusterTrackers = trackClusters ? new HashMap<Alignment, ClusterTracker>() : null;

        try(SamReader ownedReader = reader;
                Writer writer = new Writer(in, out, ownedReader, paired, false)){
            try(SAMRecordIterator records = ownedReader.iterator()){
                while(records.hasNext()){
                    SAMRecord record = records.next();

                    // always skip the reversed read
                    if(paired && record.getReadPairedFlag() && record.getSecondOfPairFlag())
                        continue;

                    totalReadCount = Math.incrementExact(totalReadCount);

                    if(record.getReadUnmappedFlag()){ // discard unmapped reads
                        unmapped = Math.incrementExact(unmapped);
                        if(keepUnmapped)
                            writer.write(record);
                        continue;
                    }

                    if(paired){
                        if(!record.getReadPairedFlag()){
                            unpaired = Math.incrementExact(unpaired);

                            if(removeUnpaired)
                                continue;
                        }

                        if(record.getReadPairedFlag() && record.getMateUnmappedFlag()){
                            unmapped = Math.incrementExact(unmapped);
                            continue;
                        }

                        if(record.getReadPairedFlag() && !record.getReferenceName().equals(record.getMateReferenceName())){
                            chimeric = Math.incrementExact(chimeric);

                            if(removeChimeric)
                                continue;
                        }
                    }

                    Alignment alignment = alignmentFor(record, paired);
                    Map<BitSet, ReadFreq> umiRead = align.get(alignment);

                    if(umiRead == null){
                        umiRead = new HashMap<BitSet, ReadFreq>(4);
                        align.put(alignment, umiRead);
                    }

                    Read read = new SAMRead(record);
                    BitSet umi = getValidatedUMI(read, k);
                    ReadFreq previous = umiRead.get(umi);

                    if(previous != null){
                        previous.read = merge.merge(read, previous.read);
                        previous.increment();
                    }else{
                        umiRead.put(umi, new ReadFreq(read, 1));
                    }

                    readCount = Math.incrementExact(readCount);
                }
            }

            System.gc(); // attempt to clear up memory before deduplicating

            System.out.println("Done reading input file into memory!");
            alignPosCount = align.size();

            Stream<Map.Entry<Alignment, Map<BitSet, ReadFreq>>> stream =
                parallel ? align.entrySet().parallelStream() : ((paired && !trackClusters) ? align.entrySet().stream().sorted((a, b) -> a.getKey().getRef().compareTo(b.getKey().getRef())) : align.entrySet().stream());

            stream.forEach(e -> {
                Data data = instantiateData(dataClass);
                ClusterTracker currTracker = new ClusterTracker(trackClusters);
                List<Read> deduped;

                if(algo instanceof Algorithm)
                    deduped = ((Algorithm)algo).apply(e.getValue(), (DataStructure)data, currTracker, umiLength, k, percentage);
                else
                    deduped = ((ParallelAlgorithm)algo).apply(e.getValue(), (ParallelDataStructure)data, currTracker, umiLength, k, percentage);

                synchronized(lock){
                    currTracker.setOffset(dedupedCount);

                    avgUMICount = Math.addExact(avgUMICount, e.getValue().size());
                    maxUMICount = Math.max(maxUMICount, e.getValue().size());
                    dedupedCount = Math.addExact(dedupedCount, deduped.size());

                    if(trackClusters){
                        clusterTrackers.put(e.getKey(), currTracker);
                    }else{
                        for(Read dedupedRead : deduped)
                            writer.write(((SAMRead)dedupedRead).toSAMRecord());
                    }
                }
            });

            // second pass to tag reads with their cluster and other stats
            if(trackClusters){
                System.gc(); // attempt to clear up memory before second pass

                System.out.println("Done with the first pass for tracking clusters!");

                try(SamReader secondReader = SamReaderFactory.makeDefault()
                            .validationStringency(ValidationStringency.SILENT)
                            .open(in);
                        SAMRecordIterator records = secondReader.iterator()){
                    while(records.hasNext()){
                        SAMRecord record = records.next();

                        if(record.getReadUnmappedFlag()) // discard unmapped reads
                            continue;

                        if(paired && ((removeUnpaired && !record.getReadPairedFlag()) // discard unpaired
                                    || (record.getReadPairedFlag() && record.getSecondOfPairFlag()) // ignore reversed reads
                                    || (record.getReadPairedFlag() && record.getMateUnmappedFlag()) // discard unmapped reads
                                    || (removeChimeric && record.getReadPairedFlag()
                                        && !record.getReferenceName().equals(record.getMateReferenceName())))){ // discard chimeric reads
                            continue;
                        }

                        Alignment alignment = alignmentFor(record, paired);
                        ClusterTracker currTracker = clusterTrackers.get(alignment);
                        Map<BitSet, ReadFreq> map = align.get(alignment);

                        Read read = new SAMRead(record);
                        BitSet umi = read.getUMI(umiLength);

                        int id = currTracker.getId(umi);
                        ClusterTracker.ClusterStats stats = currTracker.getStats(id);
                        int absId = Math.addExact(id, currTracker.getOffset());
                        SAMRecord record2 = record.deepCopy();
                        ReadFreq readFreq = map.get(umi);

                        record2.setAttribute("MI", Integer.toString(absId));
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
                }
            }
        }catch(IOException ex){
            throw new IllegalStateException("Could not close SAM input resources for " + in, ex);
        }

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

    private static void validateKnownK(int k, int requestedUmiLength){
        if(k < 0)
            throw new IllegalArgumentException("k must be non-negative, observed " + k);

        if(requestedUmiLength == 0 || requestedUmiLength < -1){
            throw new IllegalArgumentException(
                    "UMI length must be -1 for autodetection or positive, observed "
                    + requestedUmiLength
            );
        }

        if(requestedUmiLength > 0)
            validateEffectiveK(k, requestedUmiLength);
    }

    private static void validateEffectiveK(int k, int effectiveUmiLength){
        if(k >= effectiveUmiLength){
            throw new IllegalArgumentException(
                    "k must satisfy 0 <= k < effective UMI length; observed k="
                    + k + " and effective UMI length=" + effectiveUmiLength
            );
        }
    }

    private BitSet getValidatedUMI(Read read, int k){
        if(umiLength == -1){
            int detectedLength = read.getUMILength();
            validateEffectiveK(k, detectedLength);
            umiLength = detectedLength;
        }

        return read.getUMI(umiLength);
    }

    private static Data instantiateData(Class<? extends Data> dataClass){
        try{
            return dataClass.getDeclaredConstructor().newInstance();
        }catch(ReflectiveOperationException ex){
            throw new IllegalStateException(
                    "Could not instantiate data structure " + dataClass.getName(),
                    ex
            );
        }
    }

    private static Alignment alignmentFor(SAMRecord record, boolean paired){
        if(paired){
            return new PairedAlignment(
                    record.getReadNegativeStrandFlag(),
                    record.getReadNegativeStrandFlag()
                            ? record.getUnclippedEnd()
                            : record.getUnclippedStart(),
                    record.getReferenceName(),
                    record.getInferredInsertSize()
            );
        }

        return singleEndAlignment(record);
    }

    private static int incrementFrequency(int frequency){
        try{
            return Math.incrementExact(frequency);
        }catch(ArithmeticException ex){
            throw new ArithmeticException(
                    "Read frequency exceeds the supported maximum of " + Integer.MAX_VALUE
            );
        }
    }

    private static void closeSamReader(SamReader reader, File input){
        try{
            reader.close();
        }catch(IOException ex){
            throw new IllegalStateException("Could not close SAM input " + input, ex);
        }
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
                        + ". Sort the input BAM with samtools sort before UMICollapse, or run with --streaming-mode off."
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
        boolean readerClosed = false;
        Map<Alignment, StreamingAlignReads> active = new HashMap<>(MIN_ALIGN_MAP_CAPACITY);
        PriorityQueue<StreamingAlignReads> ready = new PriorityQueue<>((a, b) -> Integer.compare(a.flushStart, b.flushStart));

        umiLength = umiLengthParam;
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;
        long totalReadCount = 0L;
        long unmapped = 0L;
        long readCount = 0L;
        long alignPosCount = 0L;
        String currentRef = null;
        int lastReferenceIndex = Integer.MIN_VALUE;
        int lastAlignmentStart = Integer.MIN_VALUE;

        try{
            temporaryOut = createStreamingTemporaryFile(out);
            writer = new Writer(in, temporaryOut, reader, false, true);
            System.out.println("Using coordinate-sorted single-end streaming fast path");

            try(SAMRecordIterator records = reader.iterator()){
                while(records.hasNext()){
                    SAMRecord record = records.next();
                    totalReadCount = Math.incrementExact(totalReadCount);

                    if(record.getReadUnmappedFlag()){
                        unmapped = Math.incrementExact(unmapped);
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
                                + ". Sort the input BAM with samtools sort before UMICollapse, or run with --streaming-mode off."
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

                    Alignment alignment = streamingSingleEndAlignment(record);
                    flushReadyStreamingGroups(active, ready, writer, algo, dataClass, k, percentage, alignmentStart);

                    StreamingAlignReads alignReads = active.get(alignment);

                    if(alignReads == null){
                        alignReads = new StreamingAlignReads(alignment, streamingFlushStart(alignment));
                        active.put(alignment, alignReads);
                        ready.add(alignReads);
                        alignPosCount = Math.incrementExact(alignPosCount);
                    }

                    addStreamingRead(alignReads, record, merge, k);
                    readCount = Math.incrementExact(readCount);
                }
            }

            flushAllStreamingGroups(active, ready, writer, algo, dataClass, k, percentage);

            writerClosed = true;
            writer.close();
            readerClosed = true;
            closeSamReader(reader, in);
            promoteStreamingOutput(temporaryOut, out);
        }catch(RuntimeException | Error ex){
            if(writer != null && !writerClosed){
                try{
                    writer.close();
                }catch(RuntimeException | Error closeEx){
                    ex.addSuppressed(closeEx);
                }
            }

            if(!readerClosed){
                try{
                    closeSamReader(reader, in);
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
        int umiCount = alignReads.umiCount();

        avgUMICount = Math.addExact(avgUMICount, umiCount);
        maxUMICount = Math.max(maxUMICount, umiCount);

        // Exact-UMI merging has already selected the representative. For the
        // built-in algorithm/data-structure pairs covered by
        // canBypassSingletonClustering(), a one-UMI group cannot change. Custom
        // extensions still execute the general clustering path.
        if(umiCount == 1 && canBypassSingletonClustering(algo, dataClass)){
            writer.write(((SAMRead)alignReads.firstRead).toSAMRecord());
            dedupedCount = Math.incrementExact(dedupedCount);
            return;
        }

        Data data = instantiateData(dataClass);
        Map<BitSet, ReadFreq> umiRead = alignReads.materialize();

        List<Read> deduped = ((Algorithm)algo).apply(
                umiRead,
                (DataStructure)data,
                new ClusterTracker(false),
                umiLength,
                k,
                percentage
        );

        dedupedCount = Math.addExact(dedupedCount, deduped.size());

        for(Read read : deduped)
            writer.write(((SAMRead)read).toSAMRecord());
    }

    private void addStreamingRead(StreamingAlignReads alignReads, SAMRecord record, Merge merge, int k){
        Read read = new SAMRead(record);
        BitSet umi = getValidatedUMI(read, k);

        if(alignReads.umiRead != null){
            ReadFreq previous = alignReads.umiRead.get(umi);

            if(previous != null){
                previous.read = merge.merge(read, previous.read);
                previous.increment();
            }else{
                alignReads.umiRead.put(umi, new ReadFreq(read, 1));
            }
            return;
        }

        if(alignReads.firstUmi == null){
            alignReads.firstUmi = umi;
            alignReads.firstRead = read;
            alignReads.firstFrequency = 1;
            return;
        }

        if(alignReads.firstUmi.equals(umi)){
            alignReads.firstRead = merge.merge(read, alignReads.firstRead);
            alignReads.firstFrequency = incrementFrequency(alignReads.firstFrequency);
            return;
        }

        alignReads.umiRead = new HashMap<BitSet, ReadFreq>(4);
        alignReads.umiRead.put(
                alignReads.firstUmi,
                new ReadFreq(alignReads.firstRead, alignReads.firstFrequency)
        );
        alignReads.umiRead.put(umi, new ReadFreq(read, 1));
        alignReads.firstUmi = null;
        alignReads.firstRead = null;
        alignReads.firstFrequency = 0;
    }

    private static Alignment singleEndAlignment(SAMRecord record){
        return new Alignment(
                record.getReadNegativeStrandFlag(),
                record.getReadNegativeStrandFlag() ? record.getUnclippedEnd() : record.getUnclippedStart(),
                record.getReferenceName()
        );
    }

    private static Alignment streamingSingleEndAlignment(SAMRecord record){
        if(record.getReadNegativeStrandFlag()){
            long unclippedEnd = (long)record.getAlignmentStart()
                    + record.getCigar().getReferenceLength() - 1L;
            List<CigarElement> elements = record.getCigar().getCigarElements();

            for(int i = elements.size() - 1; i >= 0; i--){
                CigarElement element = elements.get(i);
                CigarOperator operator = element.getOperator();

                if(operator != CigarOperator.S && operator != CigarOperator.H)
                    break;

                unclippedEnd += element.getLength();
            }

            if(unclippedEnd > Integer.MAX_VALUE){
                throw new StreamingFallbackException(
                        "Streaming mode cannot represent the reverse-strand unclipped end for read "
                        + record.getReadName() + " because it exceeds " + Integer.MAX_VALUE
                        + "; use --streaming-mode off."
                );
            }

            return new Alignment(true, (int)unclippedEnd, record.getReferenceName());
        }

        Alignment alignment = singleEndAlignment(record);
        long leadingClip = (long)record.getAlignmentStart() - alignment.coord;

        if(leadingClip <= STREAMING_POSITIVE_LAG)
            return alignment;

        throw new StreamingFallbackException(
                "Streaming positive-lag window is too small for read " + record.getReadName()
                + " with " + leadingClip + " leading clipped bases; rerun with "
                + "-Dumicollapse.streaming.positiveLag=<at-least-" + leadingClip + ">"
                + " or use --streaming-mode off."
        );
    }

    private static boolean canBypassSingletonClustering(
            Algo algo,
            Class<? extends Data> dataClass){
        Class<?> algorithmClass = algo.getClass();
        boolean builtInAlgorithm = algorithmClass == Directional.class
                || algorithmClass == Adjacency.class
                || algorithmClass == ConnectedComponents.class;
        boolean builtInData = dataClass == Naive.class
                || dataClass == Combo.class
                || dataClass == Ngram.class
                || dataClass == SymmetricDelete.class
                || dataClass == Trie.class
                || dataClass == BKTree.class
                || dataClass == SortBKTree.class
                || dataClass == NgramBKTree.class
                || dataClass == SortNgramBKTree.class
                || dataClass == FenwickBKTree.class;

        return builtInAlgorithm && builtInData;
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
        // Match SAMFileWriterFactory's legacy inference: only an explicit .sam
        // destination is text SAM; every other extension is binary BAM.
        String suffix = name.endsWith(".sam") ? ".sam" : ".bam";

        try{
            return File.createTempFile(".umicollapse-stream-", suffix, parent);
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
        OutputTransaction.runAlignment(
                in,
                out,
                stagedOutput -> deduplicateAndMergeTwoPassCore(
                        in, stagedOutput, algo, dataClass, merge, umiLengthParam, k,
                        percentage, umiSeparator, paired, removeUnpaired,
                        removeChimeric, keepUnmapped, trackClusters
                )
        );
    }

    void deduplicateAndMergeTwoPassCore(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, String umiSeparator, boolean paired, boolean removeUnpaired, boolean removeChimeric, boolean keepUnmapped, boolean trackClusters){
        validateKnownK(k, umiLengthParam);
        SAMRead.setDefaultUMIPattern(umiSeparator);

        Map<Alignment, AlignReads> align = new HashMap<>(1 << 16);
        long totalReadCount = 0L;
        long unmapped = 0L;
        long unpaired = 0L;
        long chimeric = 0L;
        long readCount = 0L;
        int alignPosCount;
        umiLength = umiLengthParam;
        avgUMICount = 0;
        maxUMICount = 0;
        dedupedCount = 0;

        try(SamReader firstPass = SamReaderFactory.makeDefault()
                    .validationStringency(ValidationStringency.SILENT)
                    .open(in);
                Writer writer = new Writer(in, out, firstPass, paired, false)){
            // first pass to figure out where each alignment position ends
            try(SAMRecordIterator records = firstPass.iterator()){
                while(records.hasNext()){
                    SAMRecord record = records.next();

                    // always skip the reversed read
                    if(paired && record.getReadPairedFlag() && record.getSecondOfPairFlag())
                        continue;

                    totalReadCount = Math.incrementExact(totalReadCount);

                    if(record.getReadUnmappedFlag()){ // discard unmapped reads
                        unmapped = Math.incrementExact(unmapped);
                        if(keepUnmapped)
                            writer.write(record);
                        continue;
                    }

                    if(paired){
                        if(!record.getReadPairedFlag()){
                            unpaired = Math.incrementExact(unpaired);

                            if(removeUnpaired)
                                continue;
                        }

                        if(record.getReadPairedFlag() && record.getMateUnmappedFlag()){
                            unmapped = Math.incrementExact(unmapped);
                            continue;
                        }

                        if(record.getReadPairedFlag() && !record.getReferenceName().equals(record.getMateReferenceName())){
                            chimeric = Math.incrementExact(chimeric);

                            if(removeChimeric)
                                continue;
                        }
                    }

                    Alignment alignment = alignmentFor(record, paired);

                    if(!align.containsKey(alignment))
                        align.put(alignment, new AlignReads());

                    align.get(alignment).latest = readCount;
                    readCount = Math.incrementExact(readCount);
                }
            }

            System.gc(); // attempt to clear up memory before second pass

            System.out.println("Done with the first pass!");
            alignPosCount = align.size();

            try(SamReader secondPass = SamReaderFactory.makeDefault()
                        .validationStringency(ValidationStringency.SILENT)
                        .open(in);
                    SAMRecordIterator records = secondPass.iterator()){
                long idx = 0L;

                while(records.hasNext()){
                    SAMRecord record = records.next();

                    if(record.getReadUnmappedFlag()) // discard unmapped reads
                        continue;

                    if(paired && ((removeUnpaired && !record.getReadPairedFlag()) // discard unpaired
                                || (record.getReadPairedFlag() && record.getSecondOfPairFlag()) // ignore reversed reads
                                || (record.getReadPairedFlag() && record.getMateUnmappedFlag()) // discard unmapped reads
                                || (removeChimeric && record.getReadPairedFlag()
                                    && !record.getReferenceName().equals(record.getMateReferenceName())))){ // discard chimeric reads
                        continue;
                    }

                    Alignment alignment = alignmentFor(record, paired);
                    AlignReads alignReads = align.get(alignment);

                    if(alignReads.umiRead == null)
                        alignReads.umiRead = new HashMap<BitSet, ReadFreq>(4);

                    Read read = new SAMRead(record);
                    BitSet umi = getValidatedUMI(read, k);
                    ReadFreq previous = alignReads.umiRead.get(umi);

                    if(previous != null){
                        previous.read = merge.merge(read, previous.read);
                        previous.increment();
                    }else{
                        alignReads.umiRead.put(umi, new ReadFreq(read, 1));
                    }

                    if(idx >= alignReads.latest){
                        Data data = instantiateData(dataClass);
                        List<Read> deduped;

                        if(algo instanceof Algorithm)
                            deduped = ((Algorithm)algo).apply(alignReads.umiRead, (DataStructure)data, new ClusterTracker(trackClusters), umiLength, k, percentage);
                        else
                            deduped = ((ParallelAlgorithm)algo).apply(alignReads.umiRead, (ParallelDataStructure)data, new ClusterTracker(trackClusters), umiLength, k, percentage);

                        avgUMICount = Math.addExact(avgUMICount, alignReads.umiRead.size());
                        maxUMICount = Math.max(maxUMICount, alignReads.umiRead.size());
                        dedupedCount = Math.addExact(dedupedCount, deduped.size());

                        for(Read dedupedRead : deduped)
                            writer.write(((SAMRead)dedupedRead).toSAMRecord());

                        // done with the current alignment position, so free up memory
                        align.remove(alignment);
                    }

                    idx = Math.incrementExact(idx);
                }
            }
        }catch(IOException ex){
            throw new IllegalStateException("Could not close SAM input resources for " + in, ex);
        }

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

    private static class ReversedRead implements Comparable<ReversedRead>{
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

            return coord == a.coord;
        }

        @Override
        public int hashCode(){
            int hash = name.hashCode();
            hash = hash * HASH_CONST + ref.hashCode();
            hash = hash * HASH_CONST + coord;
            return hash;
        }

        @Override
        public int compareTo(ReversedRead other){
            if(coord != other.coord)
                return Integer.compare(coord, other.coord);

            if(ref != other.ref)
                return ref.compareTo(other.ref);

            return name.compareTo(other.name);
        }
    }

    // heavily inspired by TwoPassPairWriter from UMI-tools
    private static class Writer implements AutoCloseable{
        private boolean paired;
        private SAMFileWriter writer;
        private SamReader reader;
        private boolean indexed;

        private String ref = null;
        private HashSet<ReversedRead> set;

        public Writer(File in, File out, SamReader r, boolean paired, boolean streaming){
            SamReader pairedReader = null;
            SAMFileWriter outputWriter = null;
            boolean pairedReaderIndexed = false;

            try{
                if(paired){
                    pairedReader = SamReaderFactory.makeDefault().validationStringency(ValidationStringency.SILENT).open(in);
                    pairedReaderIndexed = pairedReader.hasIndex();
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
            this.indexed = pairedReaderIndexed;
        }

        public void write(SAMRecord record){
            if(paired){ // must be forwards read
                String currRef = record.getReferenceName();

                if(ref == null)
                    ref = currRef;

                if(!ref.equals(currRef)){
                    if(indexed && !set.isEmpty())
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

        @Override
        public void close(){
            Throwable failure = null;

            try{
                if(paired && !set.isEmpty()){
                    // Indexed inputs can normally resolve the final reference with
                    // one bounded query. Only fall back to a sequential pass when
                    // an input has no index or cross-reference mates remain.
                    if(indexed)
                        writeReversed(false);

                    if(!set.isEmpty())
                        writeReversed(true);
                }
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

            try(SAMRecordIterator iter =
                    fullPass ? reader.iterator() : reader.query(ref, 0, 0, true)){
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

                        if(set.remove(read))
                            writer.addAlignment(record);
                    }
                }
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
        public long latest;
        public Map<BitSet, ReadFreq> umiRead;

        public AlignReads(){
            this.latest = 0;
            this.umiRead = null;
        }
    }

    private static class StreamingAlignReads{
        public Alignment alignment;
        public int flushStart;
        public BitSet firstUmi;
        public Read firstRead;
        public int firstFrequency;
        public Map<BitSet, ReadFreq> umiRead;

        public StreamingAlignReads(Alignment alignment, int flushStart){
            this.alignment = alignment;
            this.flushStart = flushStart;
            this.firstUmi = null;
            this.firstRead = null;
            this.firstFrequency = 0;
            this.umiRead = null;
        }

        public int umiCount(){
            return umiRead == null ? 1 : umiRead.size();
        }

        public Map<BitSet, ReadFreq> materialize(){
            if(umiRead == null){
                umiRead = new HashMap<BitSet, ReadFreq>(4);
                umiRead.put(firstUmi, new ReadFreq(firstRead, firstFrequency));
            }

            return umiRead;
        }
    }

    private static class StreamingFallbackException extends IllegalStateException{
        private static final long serialVersionUID = 1L;

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
            if(o == null || getClass() != o.getClass())
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
        public int compareTo(Alignment o){
            if(!(o instanceof PairedAlignment))
                return super.compareTo(o);

            PairedAlignment other = (PairedAlignment)o;

            if(tlen != other.tlen)
                return Integer.compare(tlen, other.tlen);

            return super.compareTo(other);
        }
    }

    private static class Alignment implements Comparable<Alignment>{
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
            if(o == null || getClass() != o.getClass())
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
        public int compareTo(Alignment other){
            if(strand != other.strand)
                return Boolean.compare(strand, other.strand);

            if(coord != other.coord)
                return Integer.compare(coord, other.coord);

            int refComparison = ref.compareTo(other.ref);

            if(refComparison != 0)
                return refComparison;

            return getClass().getName().compareTo(other.getClass().getName());
        }
    }
}
