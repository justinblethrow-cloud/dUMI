package test;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import umicollapse.algo.Directional;
import umicollapse.data.Combo;
import umicollapse.data.DataStructure;
import umicollapse.data.Naive;
import umicollapse.data.SymmetricDelete;
import umicollapse.data.Trie;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.Utils;

public class TestNKeyRegressions{
    public static void main(String[] args){
        testGeneratedBasePreservesNMetadata();
        testNContainingDataStructureParity();
        testDirectionalTrieDoesNotGenerateAbsentNKey();
        System.out.println("Passed: N-aware generated-key regressions");
    }

    private static void testGeneratedBasePreservesNMetadata(){
        BitSet generated = new BitSet(Read.ENCODING_LENGTH);
        Utils.charSet(generated, 0, Read.UNDETERMINED);
        BitSet expectedN = Utils.toBitSet("N");

        assertTrue(generated.equals(expectedN), "generated N key omitted N metadata");
        assertTrue(generated.hashCode() == expectedN.hashCode(), "generated N key had a different hash");

        // Force a cached hash before reusing the same recursion buffer.  The
        // ordinary base must clear both the encoded N and its auxiliary mask.
        Utils.charSet(generated, 0, Read.ENCODING_MAP.get('A'));
        BitSet expectedA = Utils.toBitSet("A");

        assertTrue(generated.equals(expectedA), "ordinary base retained stale N metadata");
        assertTrue(generated.hashCode() == expectedA.hashCode(), "ordinary-base rewrite left a stale hash");
    }

    private static void testNContainingDataStructureParity(){
        Map<BitSet, Integer> frequencies = new LinkedHashMap<>();
        frequencies.put(Utils.toBitSet("ANAA"), 9);
        frequencies.put(Utils.toBitSet("AAAA"), 4);
        frequencies.put(Utils.toBitSet("ATAA"), 3);
        frequencies.put(Utils.toBitSet("NNAA"), 2);
        frequencies.put(Utils.toBitSet("CNAT"), 1);
        frequencies.put(Utils.toBitSet("GGGG"), 5);

        for(String query : new String[]{"ANAA", "AAAA", "NNAA"}){
            for(int k : new int[]{0, 1}){
                assertParity(new Combo(), frequencies, query, k, 4);
                assertParity(new SymmetricDelete(), frequencies, query, k, 4);
                assertParity(new Trie(), frequencies, query, k, 4);
            }
        }
    }

    private static void assertParity(
            DataStructure candidate,
            Map<BitSet, Integer> frequencies,
            String query,
            int k,
            int maxFreq
    ){
        DataStructure baseline = new Naive();
        baseline.init(new HashMap<>(frequencies), query.length(), k);
        candidate.init(new HashMap<>(frequencies), query.length(), k);

        BitSet queryKey = Utils.toBitSet(query);
        Set<BitSet> expected = baseline.removeNear(queryKey, k, maxFreq);
        Set<BitSet> actual = candidate.removeNear(queryKey, k, maxFreq);

        assertTrue(
            actual.equals(expected),
            candidate.getClass().getSimpleName()
                + " disagreed with Naive for N-containing query " + query
                + " at k=" + k + ": expected " + render(expected, query.length())
                + ", actual " + render(actual, query.length())
        );
    }

    private static String render(Set<BitSet> keys, int umiLength){
        StringBuilder result = new StringBuilder("[");
        boolean first = true;

        for(BitSet key : keys){
            if(!first)
                result.append(", ");
            result.append(Utils.toString(key, umiLength));
            first = false;
        }

        return result.append(']').toString();
    }

    private static void testDirectionalTrieDoesNotGenerateAbsentNKey(){
        BitSet dominant = Utils.toBitSet("NAAA");
        BitSet neighbor = Utils.toBitSet("AAAA");
        DummyRead expected = new DummyRead(dominant);
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();
        reads.put(dominant, new ReadFreq(expected, 10));
        reads.put(neighbor, new ReadFreq(new DummyRead(neighbor), 1));

        List<Read> representatives = new Directional().apply(
            reads,
            new Trie(),
            new ClusterTracker(false),
            4,
            1,
            0.5f
        );

        assertTrue(
            representatives.size() == 1 && representatives.get(0) == expected,
            "Directional + Trie did not collapse an N-containing UMI and its neighbor"
        );
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
}
