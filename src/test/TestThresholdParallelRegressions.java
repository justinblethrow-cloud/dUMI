package test;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import umicollapse.algo.Directional;
import umicollapse.algo.ParallelAdjacency;
import umicollapse.algo.ParallelAlgorithm;
import umicollapse.algo.ParallelConnectedComponents;
import umicollapse.algo.ParallelDirectional;
import umicollapse.data.ParallelNaive;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.Utils;

public class TestThresholdParallelRegressions{
    public static void main(String[] args){
        testDirectionalThresholdPreservesFloatArithmetic();
        testDirectionalThresholdSaturatesAfterWidening();
        testParallelAdjacencyTieIsIndependentOfInsertionOrder();
        testParallelDirectionalTieIsIndependentOfInsertionOrder();
        testParallelConnectedComponentTieIsIndependentOfInsertionOrder();
        System.out.println("Passed: threshold and parallel-algorithm regressions");
    }

    private static void testDirectionalThresholdPreservesFloatArithmetic(){
        int threshold = directionalThreshold(924, 0.45945945382118225f);
        assertTrue(
            threshold == 425,
            "directional threshold changed historical float multiplication: " + threshold
        );
    }

    private static void testDirectionalThresholdSaturatesAfterWidening(){
        int threshold = directionalThreshold(Integer.MAX_VALUE, 1.0f);
        assertTrue(
            threshold == Integer.MAX_VALUE,
            "directional threshold overflowed instead of saturating: " + threshold
        );
    }

    private static void testParallelAdjacencyTieIsIndependentOfInsertionOrder(){
        assertParallelAdjacencyOrder(false);
        assertParallelAdjacencyOrder(true);
    }

    private static void assertParallelAdjacencyOrder(boolean reverseInsertion){
        BitSet left = Utils.toBitSet("AAAA");
        BitSet right = Utils.toBitSet("AATT");
        BitSet shared = Utils.toBitSet("AAAT");
        BitSet expectedFirst = left.compareTo(right) <= 0 ? left : right;
        BitSet expectedSecond = left.compareTo(right) <= 0 ? right : left;
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

        List<Read> representatives = new ParallelAdjacency().apply(
            reads,
            new ParallelNaive(),
            new ClusterTracker(false),
            4,
            1,
            0.5f
        );

        assertTrue(
            representatives.size() == 2,
            "parallel adjacency produced an unexpected representative count"
        );
        assertTrue(
            representatives.get(0).getUMI(4).equals(expectedFirst)
                && representatives.get(1).getUMI(4).equals(expectedSecond),
            "parallel adjacency tie depended on map insertion order"
        );
    }

    private static void testParallelDirectionalTieIsIndependentOfInsertionOrder(){
        assertParallelClusterOrder(new ParallelDirectional(), false);
        assertParallelClusterOrder(new ParallelDirectional(), true);
    }

    private static void testParallelConnectedComponentTieIsIndependentOfInsertionOrder(){
        assertParallelClusterOrder(new ParallelConnectedComponents(), false);
        assertParallelClusterOrder(new ParallelConnectedComponents(), true);
    }

    private static void assertParallelClusterOrder(
            ParallelAlgorithm algorithm,
            boolean reverseInsertion
    ){
        BitSet first = Utils.toBitSet("AAAA");
        BitSet tiedNeighbor = Utils.toBitSet("AAAT");
        BitSet isolated = Utils.toBitSet("CCCC");
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();

        if(reverseInsertion){
            putRead(reads, isolated, 3);
            putRead(reads, tiedNeighbor, 3);
            putRead(reads, first, 3);
        }else{
            putRead(reads, first, 3);
            putRead(reads, tiedNeighbor, 3);
            putRead(reads, isolated, 3);
        }

        List<Read> representatives = algorithm.apply(
            reads,
            new ParallelNaive(),
            new ClusterTracker(false),
            4,
            1,
            1.0f
        );

        assertTrue(
            representatives.size() == 2,
            algorithm.getClass().getSimpleName()
                + " changed cluster count when insertion order was reversed"
        );
        assertTrue(
            representatives.get(0).getUMI(4).equals(first)
                && representatives.get(1).getUMI(4).equals(isolated),
            algorithm.getClass().getSimpleName()
                + " representative selection or order depended on map insertion order"
        );
    }

    private static int directionalThreshold(int frequency, float percentage){
        try{
            Method method = Directional.class.getDeclaredMethod(
                "directionalThreshold",
                int.class,
                float.class
            );
            method.setAccessible(true);
            return (Integer)method.invoke(null, frequency, percentage);
        }catch(NoSuchMethodException | IllegalAccessException e){
            throw new AssertionError("could not inspect directional threshold", e);
        }catch(InvocationTargetException e){
            throw new AssertionError("directional threshold threw unexpectedly", e.getCause());
        }
    }

    private static void putRead(Map<BitSet, ReadFreq> reads, BitSet umi, int frequency){
        reads.put(umi, new ReadFreq(new DummyRead(umi), frequency));
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
