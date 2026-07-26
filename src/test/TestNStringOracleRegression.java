package test;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import umicollapse.algo.Directional;
import umicollapse.data.DataStructure;
import umicollapse.data.Naive;
import umicollapse.data.NgramBKTree;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.Utils;

/**
 * Checks N-aware production clustering against an independent string oracle.
 *
 * Naive and optimized data structures both consume Utils.umiDist, so parity
 * between those implementations cannot by itself detect a shared encoded-
 * distance defect. These tests derive expected distances, neighborhoods, and
 * Directional partitions directly from character strings.
 */
public final class TestNStringOracleRegression{
    private static final char[] BASES = {'A', 'T', 'C', 'G', 'N'};
    private static final int SHORT_UMI_LENGTH = 4;
    private static final int NEIGHBOR_TRIALS = 250;
    private static final int DIRECTIONAL_TRIALS = 100;
    private static final List<String> ALL_SHORT_UMIS =
        enumerate("", SHORT_UMI_LENGTH);

    private TestNStringOracleRegression(){
    }

    public static void main(String[] args){
        verifyEncodedDistanceExhaustively();
        verifyNeighborQueries(SHORT_UMI_LENGTH, NEIGHBOR_TRIALS, 20260726L);
        verifyNeighborQueries(14, NEIGHBOR_TRIALS, 20260727L);
        verifyDirectionalPartitions(
            SHORT_UMI_LENGTH,
            DIRECTIONAL_TRIALS,
            20260728L
        );
        verifyDirectionalPartitions(14, DIRECTIONAL_TRIALS, 20260729L);
        verifyEqualFrequencySharedNNeighbor();
        System.out.println(
            "Passed: independent N-aware string distance and partition oracle"
        );
    }

    private static void verifyEncodedDistanceExhaustively(){
        for(String left : ALL_SHORT_UMIS){
            for(String right : ALL_SHORT_UMIS){
                int expected = stringDistance(left, right);
                int observed = Utils.umiDist(
                    Utils.toBitSet(left),
                    Utils.toBitSet(right)
                );

                if(observed != expected){
                    throw new AssertionError(
                        "encoded distance disagreed with string distance"
                    );
                }
            }
        }
    }

    private static void verifyNeighborQueries(
            int umiLength,
            int trials,
            long seed){
        Random random = new Random(seed);

        for(int trial = 0; trial < trials; trial++){
            LinkedHashMap<String, Integer> frequencies = new LinkedHashMap<>();
            int size = 1 + random.nextInt(60);

            while(frequencies.size() < size){
                String umi = umiLength == SHORT_UMI_LENGTH
                    ? ALL_SHORT_UMIS.get(random.nextInt(ALL_SHORT_UMIS.size()))
                    : randomUMI(random, umiLength);
                frequencies.put(umi, 1 + random.nextInt(12));
            }

            if(frequencies.keySet().stream().noneMatch(
                    value -> value.indexOf('N') >= 0)){
                frequencies.put(
                    "N" + randomUMI(random, umiLength - 1),
                    1 + random.nextInt(12)
                );
            }

            String presentQuery = new ArrayList<>(frequencies.keySet()).get(
                random.nextInt(frequencies.size())
            );
            String arbitraryQuery = umiLength == SHORT_UMI_LENGTH
                ? ALL_SHORT_UMIS.get(random.nextInt(ALL_SHORT_UMIS.size()))
                : randomUMI(random, umiLength);
            int maxFrequency = random.nextBoolean()
                ? Integer.MAX_VALUE
                : 1 + random.nextInt(12);

            verifyQuery(
                frequencies,
                presentQuery,
                maxFrequency,
                umiLength
            );
            verifyQuery(
                frequencies,
                arbitraryQuery,
                maxFrequency,
                umiLength
            );
        }
    }

    private static void verifyQuery(
            LinkedHashMap<String, Integer> frequencies,
            String query,
            int maxFrequency,
            int umiLength){
        Set<String> expected = new HashSet<>();

        for(Map.Entry<String, Integer> entry : frequencies.entrySet()){
            if(stringDistance(query, entry.getKey()) <= 1
                    && (entry.getValue() <= maxFrequency
                        || entry.getKey().equals(query))){
                expected.add(entry.getKey());
            }
        }

        Set<String> ngramObserved = query(
            new NgramBKTree(),
            frequencies,
            query,
            maxFrequency,
            umiLength
        );
        Set<String> naiveObserved = query(
            new Naive(),
            frequencies,
            query,
            maxFrequency,
            umiLength
        );

        if(!ngramObserved.equals(expected) || !naiveObserved.equals(expected)){
            throw new AssertionError(
                "neighbor query disagreed with independent string oracle"
            );
        }
    }

    private static Set<String> query(
            DataStructure data,
            LinkedHashMap<String, Integer> source,
            String query,
            int maxFrequency,
            int umiLength){
        Map<BitSet, Integer> encoded = new HashMap<>();

        for(Map.Entry<String, Integer> entry : source.entrySet()){
            encoded.put(
                Utils.toBitSet(entry.getKey()),
                entry.getValue()
            );
        }

        data.init(encoded, umiLength, 1);
        Set<String> observed = new HashSet<>();

        for(BitSet umi : data.removeNear(
                Utils.toBitSet(query),
                1,
                maxFrequency)){
            observed.add(Utils.toString(umi, umiLength));
        }

        return observed;
    }

    private static void verifyDirectionalPartitions(
            int umiLength,
            int trials,
            long seed){
        Random random = new Random(seed);

        for(int trial = 0; trial < trials; trial++){
            LinkedHashMap<String, Integer> frequencies = connectedFixture(
                random,
                umiLength,
                2 + random.nextInt(40)
            );
            Set<String> expected = stringDirectionalPartitions(frequencies);
            Set<String> observed = dumiDirectionalPartitions(
                frequencies,
                umiLength
            );

            if(!observed.equals(expected)){
                throw new AssertionError(
                    "Directional Ngram partition disagreed with string oracle"
                );
            }
        }
    }

    private static void verifyEqualFrequencySharedNNeighbor(){
        LinkedHashMap<String, Integer> frequencies = new LinkedHashMap<>();
        frequencies.put("NAAA", 5);
        frequencies.put("NTAA", 5);
        frequencies.put("NNAA", 1);

        Set<String> expected = new HashSet<>();
        expected.add("NAAA=5,NNAA=1");
        expected.add("NTAA=5");

        Set<String> stringObserved = stringDirectionalPartitions(frequencies);
        Set<String> dumiObserved = dumiDirectionalPartitions(
            frequencies,
            SHORT_UMI_LENGTH
        );

        if(!stringObserved.equals(expected) || !dumiObserved.equals(expected)){
            throw new AssertionError(
                "deterministic tie order did not assign the shared N neighbor "
                    + "to the expected root"
            );
        }
    }

    private static LinkedHashMap<String, Integer> connectedFixture(
            Random random,
            int umiLength,
            int size){
        LinkedHashMap<String, Integer> frequencies = new LinkedHashMap<>();
        String seed = "N" + randomUMI(random, umiLength - 1);
        frequencies.put(seed, 1 + random.nextInt(12));

        while(frequencies.size() < size){
            List<String> existing = new ArrayList<>(frequencies.keySet());
            char[] candidate = existing.get(
                random.nextInt(existing.size())
            ).toCharArray();
            int position = random.nextInt(candidate.length);
            candidate[position] = BASES[random.nextInt(BASES.length)];
            frequencies.put(
                new String(candidate),
                1 + random.nextInt(12)
            );
        }

        return frequencies;
    }

    private static Set<String> stringDirectionalPartitions(
            LinkedHashMap<String, Integer> frequencies){
        List<String> order = new ArrayList<>(frequencies.keySet());
        order.sort(
            Comparator
                .<String>comparingInt(frequencies::get)
                .reversed()
                .thenComparingLong(TestNStringOracleRegression::encodedOrder)
        );
        Set<String> remaining = new HashSet<>(frequencies.keySet());
        Set<String> partitions = new HashSet<>();

        for(String root : order){
            if(!remaining.contains(root))
                continue;

            List<String> members = new ArrayList<>();
            Deque<String> pending = new ArrayDeque<>();
            pending.push(root);

            while(!pending.isEmpty()){
                String current = pending.pop();
                int threshold = (frequencies.get(current) + 1) / 2;
                List<String> removed = new ArrayList<>();

                for(String candidate : new ArrayList<>(remaining)){
                    if(candidate.equals(current)
                            || (frequencies.get(candidate) <= threshold
                                && stringDistance(current, candidate) <= 1)){
                        remaining.remove(candidate);
                        removed.add(candidate);
                        members.add(candidate);
                    }
                }

                for(String candidate : removed){
                    if(!candidate.equals(current))
                        pending.push(candidate);
                }
            }

            partitions.add(canonicalCluster(members, frequencies));
        }

        return partitions;
    }

    private static Set<String> dumiDirectionalPartitions(
            LinkedHashMap<String, Integer> frequencies,
            int umiLength){
        Map<BitSet, ReadFreq> reads = new HashMap<>();
        Map<String, BitSet> encoded = new HashMap<>();

        for(Map.Entry<String, Integer> entry : frequencies.entrySet()){
            BitSet umi = Utils.toBitSet(entry.getKey());
            encoded.put(entry.getKey(), umi);
            reads.put(
                umi,
                new ReadFreq(
                    new SyntheticRead(umi, umiLength),
                    entry.getValue()
                )
            );
        }

        ClusterTracker tracker = new ClusterTracker(true);
        new Directional().apply(
            reads,
            new NgramBKTree(),
            tracker,
            umiLength,
            1,
            0.5f
        );
        Map<Integer, List<String>> byCluster = new HashMap<>();

        for(Map.Entry<String, BitSet> entry : encoded.entrySet()){
            byCluster.computeIfAbsent(
                tracker.getId(entry.getValue()),
                ignored -> new ArrayList<>()
            ).add(entry.getKey());
        }

        Set<String> partitions = new HashSet<>();

        for(List<String> members : byCluster.values()){
            partitions.add(canonicalCluster(members, frequencies));
        }

        return partitions;
    }

    private static String canonicalCluster(
            List<String> members,
            Map<String, Integer> frequencies){
        List<String> values = new ArrayList<>();

        for(String member : members){
            values.add(member + "=" + frequencies.get(member));
        }

        Collections.sort(values);
        return String.join(",", values);
    }

    private static long encodedOrder(String value){
        long encoded = 0L;

        for(int index = 0; index < value.length(); index++){
            long base;

            switch(value.charAt(index)){
                case 'A':
                    base = 0b000L;
                    break;
                case 'T':
                    base = 0b101L;
                    break;
                case 'C':
                    base = 0b110L;
                    break;
                case 'G':
                    base = 0b011L;
                    break;
                case 'N':
                    base = 0b100L;
                    break;
                default:
                    throw new AssertionError("unexpected synthetic UMI base");
            }

            encoded |= base << (index * 3);
        }

        return encoded;
    }

    private static int stringDistance(String left, String right){
        if(left.length() != right.length())
            throw new AssertionError("string oracle received unequal UMI lengths");

        int distance = 0;

        for(int index = 0; index < left.length(); index++){
            if(left.charAt(index) != right.charAt(index))
                distance++;
        }

        return distance;
    }

    private static String randomUMI(Random random, int length){
        StringBuilder value = new StringBuilder(length);

        for(int index = 0; index < length; index++){
            value.append(BASES[random.nextInt(BASES.length)]);
        }

        return value.toString();
    }

    private static List<String> enumerate(String prefix, int remaining){
        List<String> values = new ArrayList<>();

        if(remaining == 0){
            values.add(prefix);
            return values;
        }

        for(char base : BASES){
            values.addAll(enumerate(prefix + base, remaining - 1));
        }

        return values;
    }

    private static final class SyntheticRead extends Read{
        private final BitSet umi;
        private final int umiLength;

        private SyntheticRead(BitSet umi, int umiLength){
            this.umi = umi;
            this.umiLength = umiLength;
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
            return umiLength;
        }
    }
}
