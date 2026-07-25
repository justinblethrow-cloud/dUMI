package test;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import umicollapse.data.BKTree;
import umicollapse.data.DataStructure;
import umicollapse.data.FenwickBKTree;
import umicollapse.data.Naive;
import umicollapse.data.NgramBKTree;
import umicollapse.data.ParallelBKTree;
import umicollapse.data.ParallelDataStructure;
import umicollapse.data.ParallelFenwickBKTree;
import umicollapse.data.ParallelNaive;
import umicollapse.data.SortBKTree;
import umicollapse.data.SortNgramBKTree;
import umicollapse.util.BitSet;
import umicollapse.util.Read;
import umicollapse.util.Utils;

public class TestBKTreeDepthRegressions{
    /*
     * A default-size JVM stack cannot traverse a recursive chain this deep.
     * The synthetic keys below implement the discrete metric, so this remains
     * a valid (if deliberately degenerate) BK tree rather than relying on
     * malformed distances.
     */
    private static final int DEEP_CHAIN_SIZE = 6000;
    private static final int PARITY_UMI_LENGTH = 10;
    private static final int PARITY_MAX_EDITS = 2;

    public static void main(String[] args) throws Exception{
        testSequentialParity();
        testParallelParityAndConcurrency();
        testDegenerateDepth();
        System.out.println("Passed: iterative BK-tree depth and parity regressions");
    }

    private static void testSequentialParity(){
        Map<BitSet, Integer> input = randomInput(240, 9137L);
        List<Query> queries = parityQueries(input, 80, 1777L);
        DataStructure[] candidates = {
            new BKTree(),
            new SortBKTree(),
            new FenwickBKTree(),
            new NgramBKTree(),
            new SortNgramBKTree()
        };

        for(DataStructure candidate : candidates){
            DataStructure baseline = new Naive();
            baseline.init(new HashMap<BitSet, Integer>(input), PARITY_UMI_LENGTH, PARITY_MAX_EDITS);
            candidate.init(new HashMap<BitSet, Integer>(input), PARITY_UMI_LENGTH, PARITY_MAX_EDITS);

            for(Query query : queries){
                Set<BitSet> expected = baseline.removeNear(query.umi, query.k, query.maxFreq);
                Set<BitSet> actual = candidate.removeNear(query.umi, query.k, query.maxFreq);

                if(!expected.equals(actual)){
                    throw new AssertionError(
                        candidate.getClass().getSimpleName()
                        + " disagreed with Naive for k=" + query.k
                        + ", maxFreq=" + query.maxFreq
                        + ", expected=" + expected
                        + ", actual=" + actual
                    );
                }

                for(BitSet original : input.keySet()){
                    if(baseline.contains(original) != candidate.contains(original)){
                        throw new AssertionError(
                            candidate.getClass().getSimpleName()
                            + " membership diverged from Naive"
                        );
                    }
                }
            }
        }
    }

    private static void testParallelParityAndConcurrency()
            throws InterruptedException, ExecutionException{
        Map<BitSet, Integer> input = randomInput(300, 4211L);
        List<BitSet> queries = new ArrayList<>(input.keySet());
        ParallelDataStructure[] candidates = {
            new ParallelBKTree(),
            new ParallelFenwickBKTree()
        };

        for(ParallelDataStructure candidate : candidates){
            ParallelDataStructure baseline = new ParallelNaive();
            baseline.init(new HashMap<BitSet, Integer>(input), PARITY_UMI_LENGTH, PARITY_MAX_EDITS);
            candidate.init(new HashMap<BitSet, Integer>(input), PARITY_UMI_LENGTH, PARITY_MAX_EDITS);

            for(int i = 0; i < queries.size(); i++){
                BitSet query = queries.get(i);
                int k = i % (PARITY_MAX_EDITS + 1);
                int maxFreq = i % 4 == 0 ? 6 : Integer.MAX_VALUE;
                Set<BitSet> expected = baseline.near(query, k, maxFreq);
                Set<BitSet> actual = candidate.near(query, k, maxFreq);

                if(!expected.equals(actual)){
                    throw new AssertionError(
                        candidate.getClass().getSimpleName()
                        + " disagreed with ParallelNaive"
                    );
                }
            }

            ExecutorService executor = Executors.newFixedThreadPool(4);
            try{
                List<Callable<Void>> tasks = new ArrayList<>();

                for(int i = 0; i < 160; i++){
                    final int queryIndex = i % queries.size();
                    tasks.add(() -> {
                        BitSet query = queries.get(queryIndex);
                        int k = queryIndex % (PARITY_MAX_EDITS + 1);
                        int maxFreq = queryIndex % 4 == 0 ? 6 : Integer.MAX_VALUE;
                        Set<BitSet> expected = baseline.near(query, k, maxFreq);
                        Set<BitSet> actual = candidate.near(query, k, maxFreq);

                        if(!expected.equals(actual))
                            throw new AssertionError("concurrent BK-tree query changed shared state");

                        return null;
                    });
                }

                for(Future<Void> result : executor.invokeAll(tasks))
                    result.get();
            }finally{
                executor.shutdownNow();
            }
        }
    }

    private static void testDegenerateDepth(){
        Map<BitSet, Integer> input = new LinkedHashMap<>();
        List<BitSet> keys = new ArrayList<>(DEEP_CHAIN_SIZE);

        for(int i = 0; i < DEEP_CHAIN_SIZE; i++){
            BitSet key = new DiscreteMetricBitSet(i);
            input.put(key, 1);
            keys.add(key);
        }

        assertDeepRemoval(new BKTree(), input, keys, 1, 0);
        assertDeepRemoval(new SortBKTree(), input, keys, 1, 0);
        assertDeepRemoval(new FenwickBKTree(), input, keys, 1, 0);
        assertDeepRemoval(new NgramBKTree(), input, keys, 2, 1);
        assertDeepRemoval(new SortNgramBKTree(), input, keys, 2, 1);
        assertDeepNear(new ParallelBKTree(), input, keys, 1, 0);
        assertDeepNear(new ParallelFenwickBKTree(), input, keys, 1, 0);
    }

    private static void assertDeepRemoval(
            DataStructure candidate,
            Map<BitSet, Integer> input,
            List<BitSet> keys,
            int umiLength,
            int maxEdits){
        candidate.init(new LinkedHashMap<BitSet, Integer>(input), umiLength, maxEdits);

        if(candidate instanceof BKTree
                || candidate instanceof SortBKTree
                || candidate instanceof FenwickBKTree){
            Map<String, Float> stats = candidate.stats();
            float expectedDepth = DEEP_CHAIN_SIZE;

            if(!Float.valueOf(expectedDepth).equals(stats.get("max depth"))
                    || !Float.valueOf(expectedDepth).equals(stats.get("avg depth"))){
                throw new AssertionError(
                    candidate.getClass().getSimpleName()
                    + " reported incorrect deep-tree statistics: " + stats
                );
            }
        }

        Set<BitSet> removed = candidate.removeNear(keys.get(0), 1, Integer.MAX_VALUE);

        if(removed.size() != DEEP_CHAIN_SIZE)
            throw new AssertionError(candidate.getClass().getSimpleName() + " truncated a deep search");

        for(BitSet key : keys){
            if(candidate.contains(key))
                throw new AssertionError(candidate.getClass().getSimpleName() + " left a deep-chain key");
        }
    }

    private static void assertDeepNear(
            ParallelDataStructure candidate,
            Map<BitSet, Integer> input,
            List<BitSet> keys,
            int umiLength,
            int maxEdits){
        candidate.init(new LinkedHashMap<BitSet, Integer>(input), umiLength, maxEdits);
        Set<BitSet> nearby = candidate.near(keys.get(0), 1, Integer.MAX_VALUE);

        if(nearby.size() != DEEP_CHAIN_SIZE)
            throw new AssertionError(candidate.getClass().getSimpleName() + " truncated a deep search");
    }

    private static Map<BitSet, Integer> randomInput(int size, long seed){
        Random random = new Random(seed);
        Map<BitSet, Integer> input = new LinkedHashMap<>();

        while(input.size() < size)
            input.put(Utils.toBitSet(TestUtils.randUMI(PARITY_UMI_LENGTH, random)), 1 + random.nextInt(12));

        return input;
    }

    private static List<Query> parityQueries(Map<BitSet, Integer> input, int count, long seed){
        Random random = new Random(seed);
        List<BitSet> keys = new ArrayList<>(input.keySet());
        List<Query> queries = new ArrayList<>();

        for(int i = 0; i < count; i++){
            BitSet umi = i % 5 == 0
                ? Utils.toBitSet(TestUtils.randUMI(PARITY_UMI_LENGTH, random))
                : keys.get(random.nextInt(keys.size()));
            int k = random.nextInt(PARITY_MAX_EDITS + 1);
            int maxFreq = random.nextBoolean() ? Integer.MAX_VALUE : random.nextInt(13);
            queries.add(new Query(umi, k, maxFreq));
        }

        return queries;
    }

    private static class Query{
        private final BitSet umi;
        private final int k;
        private final int maxFreq;

        Query(BitSet umi, int k, int maxFreq){
            this.umi = umi;
            this.k = k;
            this.maxFreq = maxFreq;
        }
    }

    private static class DiscreteMetricBitSet extends BitSet{
        private final int id;

        DiscreteMetricBitSet(int id){
            super(Read.ENCODING_LENGTH);
            this.id = id;
        }

        @Override
        public int bitCountXOR(BitSet other){
            if(!(other instanceof DiscreteMetricBitSet))
                return super.bitCountXOR(other);

            DiscreteMetricBitSet key = (DiscreteMetricBitSet)other;
            return id == key.id ? 0 : Read.ENCODING_DIST;
        }

        @Override
        public boolean equals(Object other){
            return other instanceof DiscreteMetricBitSet
                && id == ((DiscreteMetricBitSet)other).id;
        }

        @Override
        public int hashCode(){
            return Integer.hashCode(id);
        }

        @Override
        public int compareTo(BitSet other){
            if(other instanceof DiscreteMetricBitSet)
                return Integer.compare(id, ((DiscreteMetricBitSet)other).id);

            return super.compareTo(other);
        }
    }
}
