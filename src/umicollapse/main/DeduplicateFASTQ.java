package umicollapse.main;

import htsjdk.samtools.fastq.FastqRecord;
import htsjdk.samtools.fastq.FastqReader;
import htsjdk.samtools.fastq.FastqWriter;
import htsjdk.samtools.fastq.FastqWriterFactory;

import java.util.Map;
import java.util.HashMap;
import java.util.List;

import java.util.stream.Stream;

import java.io.File;

import umicollapse.util.BitSet;
import umicollapse.algo.*;
import umicollapse.data.*;
import umicollapse.merge.*;
import umicollapse.util.Read;
import umicollapse.util.FASTQRead;
import umicollapse.util.ReadFreq;
import umicollapse.util.ClusterTracker;

public class DeduplicateFASTQ{
    private long uniqueCount;
    private long dedupedCount;
    private int umiLength;

    public void deduplicateAndMerge(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, boolean trackClusters){
        OutputTransaction.runFastq(
                in,
                out,
                stagedOutput -> deduplicateAndMergeCore(
                        in, stagedOutput, algo, dataClass, merge, umiLengthParam, k,
                        percentage, parallel, trackClusters
                )
        );
    }

    void deduplicateAndMergeCore(File in, File out, Algo algo, Class<? extends Data> dataClass, Merge merge, int umiLengthParam, int k, float percentage, boolean parallel, boolean trackClusters){
        if(k < 0)
            throw new IllegalArgumentException("k must be non-negative, observed " + k);

        umiLength = umiLengthParam;

        if(umiLength == -1)
            umiLength = 0;
        else if(umiLength < 0)
            throw new IllegalArgumentException("FASTQ UMI trim length must be non-negative, observed " + umiLength);

        Map<Integer, Map<BitSet, ReadFreq>> readLength = new HashMap<>(1 << 16);
        long readCount = 0L;

        try(FastqReader reader = new FastqReader(in)){
            while(reader.hasNext()){
                FastqRecord record = reader.next();
                int length = record.getReadLength();

                validateEffectiveK(k, length);

                if(umiLength > length){
                    throw new IllegalArgumentException(
                            "FASTQ UMI trim length " + umiLength
                            + " exceeds read length " + length
                            + " for read '" + record.getReadName() + "'"
                    );
                }

                if(!readLength.containsKey(length))
                    readLength.put(length, new HashMap<BitSet, ReadFreq>(4));

                Map<BitSet, ReadFreq> umiRead = readLength.get(length);

                Read read = new FASTQRead(record.getReadName(), record.getReadString(), record.getBaseQualityString());
                BitSet umi = read.getUMI(-1);
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

        uniqueCount = 0L;
        dedupedCount = 0L;
        Object lock = new Object();

        final Map<Integer, ClusterTracker> clusterTrackers = trackClusters ? new HashMap<Integer, ClusterTracker>() : null;

        try(FastqWriter writer = new FastqWriterFactory().newWriter(out)){
            Stream<Map.Entry<Integer, Map<BitSet, ReadFreq>>> stream = parallel ?
                readLength.entrySet().parallelStream() : readLength.entrySet().stream();

            stream.forEach(e -> {
                List<Read> deduped;
                Data data = instantiateData(dataClass);

                ClusterTracker currTracker = new ClusterTracker(trackClusters);

                if(algo instanceof Algorithm)
                    deduped = ((Algorithm)algo).apply(e.getValue(), ((DataStructure)data), currTracker, e.getKey(), k, percentage);
                else
                    deduped = ((ParallelAlgorithm)algo).apply(e.getValue(), ((ParallelDataStructure)data), currTracker, e.getKey(), k, percentage);

                synchronized(lock){
                    currTracker.setOffset(dedupedCount);

                    uniqueCount = Math.addExact(uniqueCount, e.getValue().size());
                    dedupedCount = Math.addExact(dedupedCount, deduped.size());

                    if(trackClusters){
                        clusterTrackers.put(e.getKey(), currTracker);
                    }else{
                        for(Read read : deduped)
                            writer.write(((FASTQRead)read).toFASTQRecord(e.getKey(), umiLength));
                    }
                }
            });

            // second pass to tag reads with their cluster and other stats
            if(trackClusters){
                System.gc(); // attempt to clear up memory before second pass

                System.out.println("Done with the first pass for tracking clusters!");

                try(FastqReader reader = new FastqReader(in)){
                    while(reader.hasNext()){
                        FastqRecord record = reader.next();
                        int length = record.getReadLength();

                        ClusterTracker currTracker = clusterTrackers.get(length);
                        Map<BitSet, ReadFreq> map = readLength.get(length);

                        Read read = new FASTQRead(record.getReadName(), record.getReadString(), record.getBaseQualityString());
                        BitSet umi = read.getUMI(-1);

                        int id = currTracker.getId(umi);
                        ClusterTracker.ClusterStats stats = currTracker.getStats(id);
                        int absId = Math.addExact(id, currTracker.getOffset());
                        StringBuffer b = new StringBuffer(record.getReadName());
                        ReadFreq readFreq = map.get(umi);

                        b.append(" cluster_id=");
                        b.append(absId);

                        if(stats.getUMI().equals(umi) && stats.getRead().equals(read)){
                            b.append(" cluster_size=");
                            b.append(stats.getFreq());
                            b.append(" same_umi=");
                            b.append(readFreq.freq);
                        }else if(readFreq.read.equals(read)){
                            b.append(" same_umi=");
                            b.append(readFreq.freq);
                        }

                        FastqRecord record2 = new FastqRecord(
                                b.toString(),
                                record.getReadString().substring(umiLength),
                                record.getBaseQualityHeader(),
                                record.getBaseQualityString().substring(umiLength)
                        );
                        writer.write(record2);
                    }
                }
            }
        }

        System.out.println("Number of input reads\t" + readCount);
        System.out.println("Number of unique reads\t" + uniqueCount);

        if(trackClusters)
            System.out.println("Number of groups of reads\t" + dedupedCount);
        else
            System.out.println("Number of reads after deduplicating\t" + dedupedCount);
    }

    private static void validateEffectiveK(int k, int effectiveUmiLength){
        if(k >= effectiveUmiLength){
            throw new IllegalArgumentException(
                    "k must satisfy 0 <= k < effective UMI length; observed k="
                    + k + " and effective UMI length=" + effectiveUmiLength
            );
        }
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
}
