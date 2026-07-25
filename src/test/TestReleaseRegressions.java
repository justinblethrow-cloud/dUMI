package test;

import htsjdk.samtools.SAMFileHeader;
import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SAMSequenceRecord;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;

import umicollapse.algo.Adjacency;
import umicollapse.algo.Algorithm;
import umicollapse.algo.ConnectedComponents;
import umicollapse.algo.Directional;
import umicollapse.algo.ParallelConnectedComponents;
import umicollapse.algo.ParallelDirectional;
import umicollapse.data.DataStructure;
import umicollapse.data.Naive;
import umicollapse.data.NgramBKTree;
import umicollapse.data.ParallelDataStructure;
import umicollapse.data.SortNgramBKTree;
import umicollapse.merge.AvgQualMerge;
import umicollapse.merge.MapQualMerge;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.FASTQRead;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.SAMRead;
import umicollapse.util.Utils;

public class TestReleaseRegressions{
    public static void main(String[] args){
        testBitSetClonePreservesNDistance();
        testBitSetOrderingIncludesNMetadata();
        testLiteralSAMSeparator();
        testMalformedSAMNameDiagnostic();
        testShortSAMUMIsRejected();
        testReadEqualityContracts();
        testDeterministicDirectionalTie();
        testDeterministicAdjacencyTie();
        testDeterministicConnectedComponentTie();
        testComponentWalksDoNotUseTheCallStack();
        testDeterministicMapQualMerge();
        testDeterministicAvgQualMerge();
        testNgramArgumentValidation();
        testPackedNgramCapacityUsesFiniteKeyUniverse();
        System.out.println("Passed: release correctness regressions");
    }

    private static void testBitSetClonePreservesNDistance(){
        BitSet original = Utils.toBitSet("AN");
        BitSet clone = original.clone();

        assertTrue(original.equals(clone), "BitSet clone changed logical identity");
        assertTrue(Utils.umiDist(original, clone) == 0, "BitSet clone changed N distance to its source");
        assertTrue(
            Utils.umiDist(clone, Utils.toBitSet("AA")) == 1,
            "BitSet clone lost N-distance metadata"
        );
    }

    private static void testBitSetOrderingIncludesNMetadata(){
        BitSet plain = Utils.toBitSet("A");
        BitSet nMarked = Utils.toBitSet("A");

        for(int i = 0; i < Read.ENCODING_LENGTH; i++)
            nMarked.setNBit(i, true);

        assertTrue(!plain.equals(nMarked), "BitSet equality ignored distance-affecting N metadata");
        assertTrue(
            plain.compareTo(nMarked) != 0 && nMarked.compareTo(plain) != 0,
            "BitSet comparison did not define a total order over N metadata"
        );
    }

    private static void testLiteralSAMSeparator(){
        SAMRead.setDefaultUMIPattern(".");
        SAMRead read = new SAMRead(samRecord("read.ACGT-X", 30, "AAAA", "IIII"));

        assertTrue(read.getUMILength() == 4, "literal dot separator was interpreted as a regex");
        assertTrue(
            read.getUMI(4).equals(Utils.toBitSet("ACGT")),
            "literal dot separator extracted the wrong UMI"
        );
    }

    private static void testMalformedSAMNameDiagnostic(){
        SAMRead.setDefaultUMIPattern(".");
        String readName = "read_ACGT-X";
        SAMRead read = new SAMRead(samRecord(readName, 30, "AAAA", "IIII"));

        expectIllegalArgument(
            () -> read.getUMILength(),
            readName,
            "literal separator '.'"
        );
    }

    private static void testShortSAMUMIsRejected(){
        SAMRead.setDefaultUMIPattern("_");
        SAMRead fastRead = new SAMRead(samRecord("read_AC-X", 30, "AAAA", "IIII"));
        expectIllegalArgument(() -> fastRead.getUMI(4), "read_AC-X", "available 2", "requested 4");

        SAMRead.setDefaultUMIPattern(".");
        SAMRead regexRead = new SAMRead(samRecord("read.AC-X", 30, "AAAA", "IIII"));
        expectIllegalArgument(() -> regexRead.getUMI(4), "read.AC-X", "available 2", "requested 4");
    }

    private static void testReadEqualityContracts(){
        SAMRecord record = samRecord("same_AAAA", 30, "AAAA", "IIII");
        SAMRead samA = new SAMRead(record);
        SAMRead samB = new SAMRead(record);

        assertTrue(samA.equals(samB) && samB.equals(samA), "SAMRead equality was not symmetric");
        assertTrue(samA.hashCode() == samB.hashCode(), "equal SAMReads had unequal hashes");
        assertTrue(!samA.equals(null) && !samA.equals("not a read"), "SAMRead equality used an unchecked cast");

        FASTQRead fastqA = new FASTQRead("same", "AAAA", "IIII");
        FASTQRead fastqB = new FASTQRead("same", "AAAA", "IIII");

        assertTrue(fastqA.equals(fastqB) && fastqB.equals(fastqA), "FASTQRead equality was not symmetric");
        assertTrue(fastqA.hashCode() == fastqB.hashCode(), "equal FASTQReads had unequal hashes");
        assertTrue(!fastqA.equals(null) && !fastqA.equals("not a read"), "FASTQRead equality used an unchecked cast");
    }

    private static void testDeterministicDirectionalTie(){
        assertSharedNeighborOwner(new Directional(), false);
        assertSharedNeighborOwner(new Directional(), true);
    }

    private static void testDeterministicAdjacencyTie(){
        assertSharedNeighborOwner(new Adjacency(), false);
        assertSharedNeighborOwner(new Adjacency(), true);
    }

    private static void assertSharedNeighborOwner(Algorithm algorithm, boolean reverseInsertion){
        BitSet left = Utils.toBitSet("AAAA");
        BitSet right = Utils.toBitSet("AATT");
        BitSet shared = Utils.toBitSet("AAAT");
        BitSet expectedOwner = left.compareTo(right) <= 0 ? left : right;
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();

        if(reverseInsertion){
            putRead(reads, right, 3);
            putRead(reads, shared, 1);
            putRead(reads, left, 3);
        }else{
            putRead(reads, left, 3);
            putRead(reads, shared, 1);
            putRead(reads, right, 3);
        }

        ClusterTracker tracker = new ClusterTracker(true);
        algorithm.apply(reads, new Naive(), tracker, 4, 1, 0.5f);
        BitSet actualOwner = tracker.getStats(tracker.getId(shared)).getUMI();

        assertTrue(
            expectedOwner.equals(actualOwner),
            algorithm.getClass().getSimpleName() + " tie depended on map insertion order"
        );
    }

    private static void testDeterministicConnectedComponentTie(){
        for(boolean reverseInsertion : new boolean[]{false, true}){
            BitSet first = Utils.toBitSet("AAAA");
            BitSet second = Utils.toBitSet("AAAT");
            BitSet expected = first.compareTo(second) <= 0 ? first : second;
            Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();
            DummyRead firstRead = new DummyRead(first);
            DummyRead secondRead = new DummyRead(second);

            if(reverseInsertion){
                reads.put(second, new ReadFreq(secondRead, 3));
                reads.put(first, new ReadFreq(firstRead, 3));
            }else{
                reads.put(first, new ReadFreq(firstRead, 3));
                reads.put(second, new ReadFreq(secondRead, 3));
            }

            List<Read> result = new ConnectedComponents().apply(
                reads,
                new Naive(),
                new ClusterTracker(false),
                4,
                1,
                0.5f
            );

            assertTrue(result.size() == 1, "connected component unexpectedly produced multiple representatives");
            assertTrue(
                result.get(0).getUMI(4).equals(expected),
                "connected-component representative tie depended on traversal order"
            );
        }
    }

    private static void testComponentWalksDoNotUseTheCallStack(){
        final int chainLength = 20_000;
        Map<BitSet, ReadFreq> reads = new HashMap<>();

        for(int i = 0; i < chainLength; i++){
            BitSet umi = new BitSet(32);

            for(int bit = 0; bit < 31; bit++)
                umi.set(bit, (i & (1 << bit)) != 0);

            reads.put(umi, new ReadFreq(new DummyRead(umi), 1));
        }

        List<Read> directional = new Directional().apply(
            reads,
            new ChainDataStructure(),
            new ClusterTracker(false),
            10,
            1,
            0.5f
        );
        List<Read> connected = new ConnectedComponents().apply(
            reads,
            new ChainDataStructure(),
            new ClusterTracker(false),
            10,
            1,
            0.5f
        );
        List<Read> parallelDirectional = new ParallelDirectional().apply(
            reads,
            new ChainParallelDataStructure(),
            new ClusterTracker(false),
            10,
            1,
            0.5f
        );
        List<Read> parallelConnected = new ParallelConnectedComponents().apply(
            reads,
            new ChainParallelDataStructure(),
            new ClusterTracker(false),
            10,
            1,
            0.5f
        );

        assertTrue(directional.size() == 1, "directional chain was not traversed as one component");
        assertTrue(connected.size() == 1, "connected-component chain was not traversed as one component");
        assertTrue(parallelDirectional.size() == 1, "parallel directional chain was not one component");
        assertTrue(parallelConnected.size() == 1, "parallel connected chain was not one component");
    }

    private static void testDeterministicMapQualMerge(){
        SAMRead alpha = new SAMRead(samRecord("alpha_AAAA", 30, "AAAA", "IIII"));
        SAMRead zeta = new SAMRead(samRecord("zeta_AAAA", 30, "TTTT", "IIII"));
        MapQualMerge merge = new MapQualMerge();

        assertTrue(merge.merge(alpha, zeta) == alpha, "equal-MAPQ merge did not select canonical record");
        assertTrue(merge.merge(zeta, alpha) == alpha, "equal-MAPQ merge depended on encounter order");

        SAMRead high = new SAMRead(samRecord("zeta_AAAA", 40, "TTTT", "IIII"));
        assertTrue(merge.merge(alpha, high) == high, "MAPQ precedence was changed by tie hardening");
    }

    private static void testDeterministicAvgQualMerge(){
        AvgQualMerge merge = new AvgQualMerge();
        SAMRead alpha = new SAMRead(samRecord("alpha_AAAA", 30, "AAAA", "IIII"));
        SAMRead zeta = new SAMRead(samRecord("zeta_AAAA", 30, "TTTT", "IIII"));

        assertTrue(merge.merge(alpha, zeta) == alpha, "equal-average SAM merge did not select canonical record");
        assertTrue(merge.merge(zeta, alpha) == alpha, "equal-average SAM merge depended on encounter order");

        FASTQRead fastqAlpha = new FASTQRead("alpha", "AAAA", "IIII");
        FASTQRead fastqZeta = new FASTQRead("zeta", "TTTT", "IIII");

        assertTrue(
            merge.merge(fastqAlpha, fastqZeta) == fastqAlpha,
            "equal-average FASTQ merge did not select canonical record"
        );
        assertTrue(
            merge.merge(fastqZeta, fastqAlpha) == fastqAlpha,
            "equal-average FASTQ merge depended on encounter order"
        );
    }

    private static void testNgramArgumentValidation(){
        Map<BitSet, Integer> oneUmi = new LinkedHashMap<>();
        oneUmi.put(Utils.toBitSet("AAAA"), 1);

        for(DataStructure candidate : new DataStructure[]{new NgramBKTree(), new SortNgramBKTree()}){
            expectIllegalArgument(() -> candidate.init(oneUmi, 0, 0), "UMI length");
            expectIllegalArgument(() -> candidate.init(oneUmi, 4, -1), "maxEdits");
            expectIllegalArgument(() -> candidate.init(oneUmi, 4, 4), "maxEdits", "UMI length");

            candidate.init(new LinkedHashMap<>(oneUmi), 4, 1);
            expectIllegalArgument(
                () -> candidate.removeNear(Utils.toBitSet("AAAA"), -1, 1),
                "k"
            );
            expectIllegalArgument(
                () -> candidate.removeNear(Utils.toBitSet("AAAA"), 2, 1),
                "k",
                "maxEdits"
            );
        }
    }

    private static void testPackedNgramCapacityUsesFiniteKeyUniverse(){
        try{
            java.lang.reflect.Method estimate = NgramBKTree.class.getDeclaredMethod(
                "expectedPackedNgramEntries",
                int.class,
                int.class,
                int.class,
                int.class
            );
            estimate.setAccessible(true);
            int actual = (Integer)estimate.invoke(null, 1_000_000, 12, 6, 1);

            // Two distinct six-base intervals each have only 5^6 keys.
            assertTrue(
                actual == 2 * 15_625,
                "packed N-gram capacity ignored the finite interval-key universe: " + actual
            );
        }catch(ReflectiveOperationException e){
            throw new AssertionError("could not inspect packed N-gram capacity estimate", e);
        }
    }

    private static void putRead(Map<BitSet, ReadFreq> reads, BitSet umi, int frequency){
        reads.put(umi, new ReadFreq(new DummyRead(umi), frequency));
    }

    private static SAMRecord samRecord(String name, int mapQuality, String bases, String qualities){
        SAMFileHeader header = new SAMFileHeader();
        header.addSequence(new SAMSequenceRecord("chr1", 1000));
        SAMRecord record = new SAMRecord(header);
        record.setReadName(name);
        record.setFlags(0);
        record.setReferenceName("chr1");
        record.setAlignmentStart(1);
        record.setMappingQuality(mapQuality);
        record.setCigarString(bases.length() + "M");
        record.setMateReferenceName(SAMRecord.NO_ALIGNMENT_REFERENCE_NAME);
        record.setMateAlignmentStart(SAMRecord.NO_ALIGNMENT_START);
        record.setInferredInsertSize(0);
        record.setReadString(bases);
        record.setBaseQualityString(qualities);
        return record;
    }

    private static void expectIllegalArgument(Runnable operation, String... messageParts){
        try{
            operation.run();
            throw new AssertionError("expected IllegalArgumentException");
        }catch(IllegalArgumentException expected){
            for(String messagePart : messageParts){
                if(expected.getMessage() == null || !expected.getMessage().contains(messagePart)){
                    throw new AssertionError(
                        "exception message omitted '" + messagePart + "': " + expected.getMessage(),
                        expected
                    );
                }
            }
        }
    }

    private static void assertTrue(boolean condition, String message){
        if(!condition)
            throw new AssertionError(message);
    }

    private static class DummyRead extends Read{
        private final BitSet umi;

        DummyRead(BitSet umi){
            this.umi = umi;
        }

        @Override
        public int getAvgQual(){
            return 0;
        }

        @Override
        public BitSet getUMI(int maxLength){
            return umi;
        }

        @Override
        public int getUMILength(){
            return 4;
        }
    }

    private static class ChainDataStructure implements DataStructure{
        private Map<BitSet, BitSet> successor;
        private Set<BitSet> remaining;

        @Override
        public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
            List<BitSet> ordered = new ArrayList<>(umiFreq.keySet());
            Collections.sort(ordered);
            successor = new HashMap<>();
            remaining = new HashSet<>(ordered);

            for(int i = 0; i + 1 < ordered.size(); i++)
                successor.put(ordered.get(i), ordered.get(i + 1));
        }

        @Override
        public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
            Set<BitSet> removed = new LinkedHashSet<>();

            if(remaining.remove(umi))
                removed.add(umi);

            BitSet next = successor.get(umi);
            if(next != null && remaining.remove(next))
                removed.add(next);

            return removed;
        }

        @Override
        public boolean contains(BitSet umi){
            return remaining.contains(umi);
        }

        @Override
        public Map<String, Float> stats(){
            return Collections.emptyMap();
        }
    }

    private static class ChainParallelDataStructure implements ParallelDataStructure{
        private Map<BitSet, BitSet> successor;

        @Override
        public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
            List<BitSet> ordered = new ArrayList<>(umiFreq.keySet());
            Collections.sort(ordered);
            successor = new HashMap<>();

            for(int i = 0; i + 1 < ordered.size(); i++)
                successor.put(ordered.get(i), ordered.get(i + 1));
        }

        @Override
        public Set<BitSet> near(BitSet umi, int k, int maxFreq){
            Set<BitSet> result = new LinkedHashSet<>();
            result.add(umi);
            BitSet next = successor.get(umi);

            if(next != null)
                result.add(next);

            return result;
        }
    }
}
