package test;

import htsjdk.samtools.SAMFileWriter;
import htsjdk.samtools.SAMFileWriterFactory;
import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.ValidationStringency;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import umicollapse.algo.Algorithm;
import umicollapse.algo.Directional;
import umicollapse.data.DataStructure;
import umicollapse.data.NgramBKTree;
import umicollapse.main.DeduplicateSAM;
import umicollapse.merge.AnyMerge;
import umicollapse.merge.MapQualMerge;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;

public class TestDeduplicateSAMHardening{
    private static final String SELECTED_PAIR = "selected_AAAA";

    public static void main(String[] args) throws Exception{
        Path temp = Files.createTempDirectory("dumi-deduplicate-sam-test.");

        try{
            testPairedMateRecovery(temp);
            testStreamingUnknownExtensionAndSingletonExtensions(temp);
            testEqualityContracts();
        }finally{
            deleteRecursively(temp);
        }

        System.out.println("Passed: DeduplicateSAM hardening regressions");
    }

    private static void testPairedMateRecovery(Path temp) throws Exception{
        File pairedSam = temp.resolve("paired.sam").toFile();
        Files.write(
                pairedSam.toPath(),
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:1000",
                        "discarded_AAAA\t99\tchr1\t100\t10\t10M\t=\t150\t60\tAAAAAAAAAA\tIIIIIIIIII",
                        SELECTED_PAIR + "\t99\tchr1\t100\t60\t10M\t=\t150\t60\tCCCCCCCCCC\tIIIIIIIIII",
                        "discarded_AAAA\t147\tchr1\t150\t55\t10M\t=\t100\t-60\tTTTTTTTTTT\tIIIIIIIIII",
                        SELECTED_PAIR + "\t147\tchr1\t150\t5\t10M\t=\t100\t-60\tGGGGGGGGGG\tIIIIIIIIII"
                ),
                StandardCharsets.US_ASCII
        );

        File samOutput = temp.resolve("paired-output.sam").toFile();
        deduplicatePaired(pairedSam, samOutput);
        assertSelectedPair(samOutput, "paired SAM");

        File unindexedBam = temp.resolve("paired-unindexed.bam").toFile();
        writeBam(pairedSam, unindexedBam, false);

        try(SamReader reader = open(unindexedBam)){
            if(reader.hasIndex())
                throw new AssertionError("test BAM unexpectedly has an index");
        }

        File bamOutput = temp.resolve("paired-unindexed-output.bam").toFile();
        deduplicatePaired(unindexedBam, bamOutput);
        assertSelectedPair(bamOutput, "paired unindexed BAM");

        File indexedBam = temp.resolve("paired-indexed.bam").toFile();
        writeBam(pairedSam, indexedBam, true);

        try(SamReader reader = open(indexedBam)){
            if(!reader.hasIndex())
                throw new AssertionError("test BAM is missing its requested index");
        }

        File indexedBamOutput = temp.resolve("paired-indexed-output.bam").toFile();
        deduplicatePaired(indexedBam, indexedBamOutput);
        assertSelectedPair(indexedBamOutput, "paired indexed BAM");
    }

    private static void testStreamingUnknownExtensionAndSingletonExtensions(Path temp) throws Exception{
        File singletonSam = temp.resolve("singleton.sam").toFile();
        Files.write(
                singletonSam.toPath(),
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:1000",
                        "single_AAAA\t0\tchr1\t100\t60\t10M\t*\t0\t0\tAAAAAAAAAA\tIIIIIIIIII"
                ),
                StandardCharsets.US_ASCII
        );

        CountingData.constructorCalls = 0;
        DropAllAlgorithm.invocations = 0;
        assertStreamingAccumulatorIsLazy();
        File streamingOutput = temp.resolve("singleton.out").toFile();
        new DeduplicateSAM().deduplicateAndMerge(
                singletonSam,
                streamingOutput,
                new DropAllAlgorithm(),
                CountingData.class,
                new AnyMerge(),
                4,
                1,
                0.5f,
                false,
                "_",
                false,
                false,
                false,
                false,
                false,
                "on"
        );

        File legacyOutput = temp.resolve("singleton-off.sam").toFile();
        new DeduplicateSAM().deduplicateAndMerge(
                singletonSam,
                legacyOutput,
                new DropAllAlgorithm(),
                CountingData.class,
                new AnyMerge(),
                4,
                1,
                0.5f,
                false,
                "_",
                false,
                false,
                false,
                false,
                false,
                "off"
        );

        if(CountingData.constructorCalls != 2 || DropAllAlgorithm.invocations != 2){
            throw new AssertionError(
                    "custom singleton extensions were not invoked equally on streaming and legacy paths"
            );
        }

        try(SamReader reader = open(streamingOutput)){
            if(reader.type() != SamReader.Type.BAM_TYPE)
                throw new AssertionError("unknown-extension streaming output was not BAM");

            int count = 0;
            for(SAMRecord ignored : reader)
                count++;

            if(count != 0)
                throw new AssertionError("custom streaming singleton emitted " + count + " records");
        }

        try(SamReader reader = open(legacyOutput)){
            int count = 0;
            for(SAMRecord ignored : reader)
                count++;

            if(count != 0)
                throw new AssertionError("custom legacy singleton emitted " + count + " records");
        }
    }

    private static void assertStreamingAccumulatorIsLazy() throws Exception{
        Class<?> alignmentClass = Class.forName("umicollapse.main.DeduplicateSAM$Alignment");
        Constructor<?> alignmentConstructor = alignmentClass.getDeclaredConstructor(
                boolean.class, int.class, String.class
        );
        alignmentConstructor.setAccessible(true);
        Object alignment = alignmentConstructor.newInstance(false, 100, "chr1");

        Class<?> accumulatorClass =
                Class.forName("umicollapse.main.DeduplicateSAM$StreamingAlignReads");
        Constructor<?> accumulatorConstructor = accumulatorClass.getDeclaredConstructor(
                alignmentClass, int.class
        );
        accumulatorConstructor.setAccessible(true);
        Object accumulator = accumulatorConstructor.newInstance(alignment, 100);

        Field umiRead = accumulatorClass.getDeclaredField("umiRead");
        umiRead.setAccessible(true);
        if(umiRead.get(accumulator) != null)
            throw new AssertionError("streaming accumulator eagerly allocated its UMI map");
    }

    private static void testEqualityContracts() throws Exception{
        Class<?> reversedClass = Class.forName("umicollapse.main.DeduplicateSAM$ReversedRead");
        Constructor<?> reversedConstructor = reversedClass.getDeclaredConstructor(
                String.class, String.class, int.class
        );
        reversedConstructor.setAccessible(true);

        Object reversedA = reversedConstructor.newInstance("read", "chr1", 10);
        Object reversedA2 = reversedConstructor.newInstance("read", "chr1", 10);
        Object reversedOtherCoordinate = reversedConstructor.newInstance("read", "chr1", 20);

        assertEqualWithSameHash(reversedA, reversedA2, "equal reversed-read keys");
        assertNotEqualBothWays(reversedA, reversedOtherCoordinate, "reversed-read coordinate");

        Class<?> alignmentClass = Class.forName("umicollapse.main.DeduplicateSAM$Alignment");
        Constructor<?> alignmentConstructor = alignmentClass.getDeclaredConstructor(
                boolean.class, int.class, String.class
        );
        alignmentConstructor.setAccessible(true);

        Class<?> pairedClass = Class.forName("umicollapse.main.DeduplicateSAM$PairedAlignment");
        Constructor<?> pairedConstructor = pairedClass.getDeclaredConstructor(
                boolean.class, int.class, String.class, int.class
        );
        pairedConstructor.setAccessible(true);

        Object alignment = alignmentConstructor.newInstance(false, 100, "chr1");
        Object paired = pairedConstructor.newInstance(false, 100, "chr1", 50);
        Object paired2 = pairedConstructor.newInstance(false, 100, "chr1", 50);
        Object pairedOtherTlen = pairedConstructor.newInstance(false, 100, "chr1", 60);

        assertNotEqualBothWays(alignment, paired, "base/paired alignment classes");
        assertEqualWithSameHash(paired, paired2, "equal paired alignments");
        assertNotEqualBothWays(paired, pairedOtherTlen, "paired-alignment template length");

        Method alignmentCompare = alignmentClass.getDeclaredMethod("compareTo", alignmentClass);
        Method pairedCompare = pairedClass.getDeclaredMethod("compareTo", alignmentClass);
        alignmentCompare.setAccessible(true);
        pairedCompare.setAccessible(true);
        int baseToPaired = (Integer)alignmentCompare.invoke(alignment, paired);
        int pairedToBase = (Integer)pairedCompare.invoke(paired, alignment);

        if(baseToPaired == 0 || Integer.signum(baseToPaired) != -Integer.signum(pairedToBase))
            throw new AssertionError("base/paired alignment ordering is not antisymmetric");
    }

    private static void deduplicatePaired(File input, File output){
        new DeduplicateSAM().deduplicateAndMerge(
                input,
                output,
                new Directional(),
                NgramBKTree.class,
                new MapQualMerge(),
                4,
                1,
                0.5f,
                false,
                "_",
                true,
                false,
                false,
                false,
                false,
                "off"
        );
    }

    private static void writeBam(File input, File output, boolean createIndex) throws Exception{
        try(SamReader reader = open(input);
                SAMFileWriter writer = new SAMFileWriterFactory()
                        .setCreateIndex(createIndex)
                        .makeBAMWriter(reader.getFileHeader(), true, output)){
            for(SAMRecord record : reader)
                writer.addAlignment(record);
        }
    }

    private static void assertSelectedPair(File file, String description) throws Exception{
        boolean sawForward = false;
        boolean sawReverse = false;
        int count = 0;

        try(SamReader reader = open(file)){
            for(SAMRecord record : reader){
                count++;

                if(!SELECTED_PAIR.equals(record.getReadName()))
                    throw new AssertionError(
                            description + " retained the unselected pair " + record.getReadName()
                    );

                if(record.getFirstOfPairFlag()){
                    if(sawForward
                            || record.getAlignmentStart() != 100
                            || record.getMappingQuality() != 60
                            || !"CCCCCCCCCC".equals(record.getReadString())){
                        throw new AssertionError(description + " retained the wrong forward record");
                    }
                    sawForward = true;
                }else if(record.getSecondOfPairFlag()){
                    if(sawReverse
                            || record.getAlignmentStart() != 150
                            || record.getMappingQuality() != 5
                            || !"GGGGGGGGGG".equals(record.getReadString())){
                        throw new AssertionError(description + " recovered the wrong reverse mate");
                    }
                    sawReverse = true;
                }else{
                    throw new AssertionError(description + " emitted a record without a pair-end flag");
                }
            }
        }

        if(count != 2 || !sawForward || !sawReverse)
            throw new AssertionError(description + " did not emit exactly the selected complete pair");
    }

    private static SamReader open(File file){
        return SamReaderFactory.makeDefault()
                .validationStringency(ValidationStringency.STRICT)
                .open(file);
    }

    private static void assertEqualWithSameHash(Object first, Object second, String description){
        if(!first.equals(second) || !second.equals(first))
            throw new AssertionError(description + " were not symmetrically equal");
        if(first.hashCode() != second.hashCode())
            throw new AssertionError(description + " had different hash codes");
    }

    private static void assertNotEqualBothWays(Object first, Object second, String description){
        if(first.equals(second) || second.equals(first))
            throw new AssertionError(description + " compared equal");
    }

    private static void deleteRecursively(Path root) throws Exception{
        if(!Files.exists(root))
            return;

        try(java.util.stream.Stream<Path> paths = Files.walk(root)){
            Path[] ordered = paths.sorted((a, b) -> b.compareTo(a)).toArray(Path[]::new);
            for(Path path : ordered)
                Files.deleteIfExists(path);
        }
    }

    public static class CountingData implements DataStructure{
        static int constructorCalls;

        public CountingData(){
            constructorCalls++;
        }

        @Override
        public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
            throw new AssertionError("streaming singleton initialized its data structure");
        }

        @Override
        public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
            throw new AssertionError("streaming singleton queried its data structure");
        }

        @Override
        public boolean contains(BitSet umi){
            throw new AssertionError("streaming singleton queried its data structure");
        }

        @Override
        public Map<String, Float> stats(){
            return new HashMap<>();
        }
    }

    public static class DropAllAlgorithm implements Algorithm{
        static int invocations;

        @Override
        public List<Read> apply(
                Map<BitSet, ReadFreq> reads,
                DataStructure data,
                ClusterTracker tracker,
                int umiLength,
                int k,
                float percentage){
            invocations++;
            return Collections.emptyList();
        }
    }
}
