package test;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import umicollapse.algo.Algorithm;
import umicollapse.algo.ConnectedComponents;
import umicollapse.algo.Directional;
import umicollapse.algo.ParallelAlgorithm;
import umicollapse.algo.ParallelConnectedComponents;
import umicollapse.algo.ParallelDirectional;
import umicollapse.data.Naive;
import umicollapse.data.ParallelNaive;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.Utils;

public class TestParallelTraversalScheduling{
    public static void main(String[] args){
        testDenseDirectionalTraversalSchedulesEachNodeOnce();
        testDenseConnectedTraversalSchedulesEachNodeOnce();
        testDirectionalParityAndDeterminism();
        testConnectedParityAndDeterminism();
        System.out.println("Passed: parallel traversal scheduling regressions");
    }

    private static void testDenseDirectionalTraversalSchedulesEachNodeOnce(){
        DenseFixture fixture = denseFixture();
        CountingSet visited = new CountingSet();

        invokeTraversal(
            new ParallelDirectional(),
            "visitAndRemove",
            new Class<?>[]{BitSet.class, Map.class, Set.class},
            fixture.nodes.get(0),
            fixture.adjacency,
            visited
        );

        assertTrue(
            visited.addAttempts == fixture.nodes.size(),
            "parallel directional scheduled duplicate pending nodes: "
                + visited.addAttempts + " attempts for " + fixture.nodes.size() + " nodes"
        );
    }

    private static void testDenseConnectedTraversalSchedulesEachNodeOnce(){
        DenseFixture fixture = denseFixture();
        CountingSet visited = new CountingSet();
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();

        for(BitSet umi : fixture.nodes)
            reads.put(umi, new ReadFreq(new DummyRead(umi), 1));

        invokeTraversal(
            new ParallelConnectedComponents(),
            "visitAndRemove",
            new Class<?>[]{BitSet.class, Map.class, Map.class, Set.class},
            fixture.nodes.get(0),
            reads,
            fixture.adjacency,
            visited
        );

        assertTrue(
            visited.addAttempts == fixture.nodes.size(),
            "parallel connected components scheduled duplicate pending nodes: "
                + visited.addAttempts + " attempts for " + fixture.nodes.size() + " nodes"
        );
    }

    private static void testDirectionalParityAndDeterminism(){
        assertParity(
            new Directional(),
            new ParallelDirectional(),
            false,
            "parallel directional"
        );
        assertParity(
            new Directional(),
            new ParallelDirectional(),
            true,
            "parallel directional"
        );
    }

    private static void testConnectedParityAndDeterminism(){
        assertParity(
            new ConnectedComponents(),
            new ParallelConnectedComponents(),
            false,
            "parallel connected components"
        );
        assertParity(
            new ConnectedComponents(),
            new ParallelConnectedComponents(),
            true,
            "parallel connected components"
        );
    }

    private static void assertParity(
        Algorithm sequential,
        ParallelAlgorithm parallel,
        boolean reverseInsertion,
        String label
    ){
        List<String> umis = Arrays.asList("AAAA", "AAAT", "AATT", "TTTT", "TTTA");
        int[] frequencies = {10, 4, 2, 8, 1};
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();

        if(reverseInsertion){
            for(int i = umis.size() - 1; i >= 0; i--)
                putRead(reads, umis.get(i), frequencies[i]);
        }else{
            for(int i = 0; i < umis.size(); i++)
                putRead(reads, umis.get(i), frequencies[i]);
        }

        List<Read> expected = sequential.apply(
            reads,
            new Naive(),
            new ClusterTracker(false),
            4,
            1,
            0.5f
        );
        List<Read> actual = parallel.apply(
            reads,
            new ParallelNaive(),
            new ClusterTracker(false),
            4,
            1,
            0.5f
        );

        assertTrue(
            representativeUmis(actual).equals(representativeUmis(expected)),
            label + " disagreed with sequential behavior"
                + (reverseInsertion ? " after reverse insertion" : "")
                + ": expected " + representativeUmis(expected)
                + ", actual " + representativeUmis(actual)
        );
    }

    private static DenseFixture denseFixture(){
        List<BitSet> nodes = Arrays.asList(
            Utils.toBitSet("AAAA"),
            Utils.toBitSet("AAAT"),
            Utils.toBitSet("AATA"),
            Utils.toBitSet("ATAA"),
            Utils.toBitSet("TAAA"),
            Utils.toBitSet("AATT"),
            Utils.toBitSet("ATAT"),
            Utils.toBitSet("TAAT")
        );
        Map<BitSet, Set<BitSet>> adjacency = new LinkedHashMap<>();

        for(BitSet node : nodes)
            adjacency.put(node, new LinkedHashSet<>(nodes));

        return new DenseFixture(nodes, adjacency);
    }

    private static List<BitSet> representativeUmis(List<Read> reads){
        List<BitSet> result = new ArrayList<>(reads.size());

        for(Read read : reads)
            result.add(read.getUMI(4));

        return result;
    }

    private static void putRead(Map<BitSet, ReadFreq> reads, String umi, int frequency){
        BitSet encoded = Utils.toBitSet(umi);
        reads.put(encoded, new ReadFreq(new DummyRead(encoded), frequency));
    }

    private static void invokeTraversal(
        Object algorithm,
        String methodName,
        Class<?>[] parameterTypes,
        Object... arguments
    ){
        try{
            Method method = algorithm.getClass().getDeclaredMethod(methodName, parameterTypes);
            method.setAccessible(true);
            method.invoke(algorithm, arguments);
        }catch(NoSuchMethodException | IllegalAccessException e){
            throw new AssertionError("could not inspect parallel traversal", e);
        }catch(InvocationTargetException e){
            throw new AssertionError("parallel traversal threw unexpectedly", e.getCause());
        }
    }

    private static void assertTrue(boolean condition, String message){
        if(!condition)
            throw new AssertionError(message);
    }

    private static class CountingSet extends HashSet<BitSet>{
        private static final long serialVersionUID = 1L;
        int addAttempts;

        @Override
        public boolean add(BitSet umi){
            addAttempts++;
            return super.add(umi);
        }
    }

    private static class DenseFixture{
        final List<BitSet> nodes;
        final Map<BitSet, Set<BitSet>> adjacency;

        DenseFixture(List<BitSet> nodes, Map<BitSet, Set<BitSet>> adjacency){
            this.nodes = nodes;
            this.adjacency = adjacency;
        }
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
