package test;

import java.util.Map;
import java.util.HashMap;
import java.util.Set;
import java.util.HashSet;

import umicollapse.util.BitSet;
import umicollapse.util.Utils;
import umicollapse.data.*;

public class TestParallelDataStructures{
    public static void main(String[] args){
        ParallelDataStructure baseline = new ParallelNaive();
        ParallelDataStructure[] data = {
            new ParallelBKTree(),
            new ParallelFenwickBKTree()
        };

        String[] s1 = {"AAAA", "AAAT", "CCCC", "CCCG", "TTTT"};
        test(s1, 0, baseline, data);

        String[] s2 = {"AAAA", "AAAT", "CCCC", "CCCG", "TTTT"};
        test(s2, 1, baseline, data);
    }

    private static void test(String[] umiList, int k, ParallelDataStructure baseline, ParallelDataStructure[] data){
        Map<BitSet, Integer> m = new HashMap<>();
        int umiLength = umiList[0].length();

        for(String umi : umiList)
            m.put(Utils.toBitSet(umi), 0);

        baseline.init(new HashMap<BitSet, Integer>(m), umiLength, k);

        for(ParallelDataStructure d : data)
            d.init(new HashMap<BitSet, Integer>(m), umiLength, k);

        for(BitSet umi : m.keySet()){
            Set<BitSet> baselineSet = baseline.near(umi, k, Integer.MAX_VALUE);

            for(ParallelDataStructure d : data){
                Set<BitSet> set = d.near(umi, k, Integer.MAX_VALUE);

                if(TestUtils.setMatches(set, baselineSet)){
                    System.out.println("Passed: data structure\t" + d.getClass().getName());
                }else{
                    throw new AssertionError(
                            "Parallel data structure " + d.getClass().getName()
                            + " disagreed with baseline for k=" + k
                            + ", query=" + Utils.toString(umi, umiLength)
                            + ", expected=" + baselineSet
                            + ", actual=" + set
                    );
                }
            }
        }
    }
}
