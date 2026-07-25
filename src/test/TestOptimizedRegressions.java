package test;

import htsjdk.samtools.SAMFileHeader;
import htsjdk.samtools.SAMRecord;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import umicollapse.algo.Directional;
import umicollapse.data.NgramBKTree;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.SAMRead;
import umicollapse.util.Utils;

public class TestOptimizedRegressions{
    public static void main(String[] args){
        testBoundedUnderscoreUMI();
        testShortUnderscoreUMIRejected();
        testDirectionalSingletonInitializesData();
        System.out.println("Passed: optimized regression tests");
    }

    private static void testBoundedUnderscoreUMI(){
        SAMRecord record = new SAMRecord(new SAMFileHeader());
        record.setReadName("read_ACGT-X");
        SAMRead.setDefaultUMIPattern("_");
        SAMRead read = new SAMRead(record);

        BitSet actual = read.getUMI(2);
        BitSet expected = Utils.toBitSet("AC");

        if(read.getUMILength() != 4 || !actual.equals(expected))
            throw new AssertionError("bounded underscore UMI parsing crossed a non-UMI delimiter");
    }

    private static void testShortUnderscoreUMIRejected(){
        SAMRecord record = new SAMRecord(new SAMFileHeader());
        record.setReadName("read_AC-X");
        SAMRead.setDefaultUMIPattern("_");
        SAMRead read = new SAMRead(record);

        try{
            read.getUMI(4);
            throw new AssertionError("short UMI was silently padded/aliased");
        }catch(IllegalArgumentException expected){
            if(!expected.getMessage().contains("read_AC-X")
                    || !expected.getMessage().contains("available 2")
                    || !expected.getMessage().contains("requested 4"))
                throw new AssertionError("short-UMI diagnostic omitted read-specific lengths", expected);
        }
    }

    private static void testDirectionalSingletonInitializesData(){
        BitSet umi = Utils.toBitSet("AAAA");
        Map<BitSet, ReadFreq> reads = new HashMap<>();
        DummyRead expected = new DummyRead(umi);
        reads.put(umi, new ReadFreq(expected, 3));

        NgramBKTree data = new NgramBKTree();
        List<Read> result = new Directional().apply(
                reads,
                data,
                new ClusterTracker(false),
                4,
                1,
                0.5f
        );

        if(result.size() != 1 || result.get(0) != expected || data.contains(umi))
            throw new AssertionError("directional singleton shortcut did not preserve initialized/consumed data state");
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
