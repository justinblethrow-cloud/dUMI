package test;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import umicollapse.data.DataStructure;
import umicollapse.data.Naive;
import umicollapse.data.NgramBKTree;
import umicollapse.util.BitSet;
import umicollapse.util.Utils;

public class TestNgramBKTreeRegression{
    private static final int[] UMI_LENGTHS = {4, 16, 17, 24, 32, 33, 64};

    public static void main(String[] args){
        for(int umiLength : UMI_LENGTHS){
            for(int maxEdits = 0; maxEdits <= 2; maxEdits++){
                if(umiLength < maxEdits + 1)
                    continue;

                for(int seed = 1; seed <= 4; seed++)
                    compareScenario(umiLength, maxEdits, seed);
            }
        }

        // Exercise the packed-key length limit and the 8-bit position fallback.
        compareScenario(255, 16, 1);
        compareScenario(256, 15, 1);

        System.out.println("Passed: randomized NgramBKTree regression matrix");
    }

    private static void compareScenario(int umiLength, int maxEdits, int seed){
        Random random = new Random(31L * umiLength + 101L * maxEdits + seed);
        Map<BitSet, Integer> input = new HashMap<>();

        while(input.size() < 120)
            input.put(Utils.toBitSet(TestUtils.randUMI(umiLength, random)), 1 + random.nextInt(25));

        DataStructure baseline = new Naive();
        DataStructure candidate = new NgramBKTree();
        baseline.init(new HashMap<BitSet, Integer>(input), umiLength, maxEdits);
        candidate.init(new HashMap<BitSet, Integer>(input), umiLength, maxEdits);

        List<BitSet> queries = new ArrayList<>(input.keySet());
        for(int i = 0; i < 20; i++)
            queries.add(Utils.toBitSet(TestUtils.randUMI(umiLength, random)));
        Collections.shuffle(queries, random);

        for(BitSet query : queries){
            int k = maxEdits == 0 ? 0 : random.nextInt(maxEdits + 1);
            int maxFreq = random.nextBoolean() ? Integer.MAX_VALUE : random.nextInt(26);
            Set<BitSet> expected = baseline.removeNear(query, k, maxFreq);
            Set<BitSet> actual = candidate.removeNear(query, k, maxFreq);

            if(!expected.equals(actual)){
                throw new AssertionError(
                        "NgramBKTree mismatch for length=" + umiLength
                        + ", maxEdits=" + maxEdits
                        + ", k=" + k
                        + ", maxFreq=" + maxFreq
                        + ", seed=" + seed
                        + ", expected=" + expected
                        + ", actual=" + actual
                );
            }

            for(BitSet original : input.keySet()){
                if(baseline.contains(original) != candidate.contains(original))
                    throw new AssertionError("NgramBKTree membership diverged after removal sequence");
            }
        }
    }
}
