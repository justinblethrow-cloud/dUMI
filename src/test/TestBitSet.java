package test;

import umicollapse.util.BitSet;
import umicollapse.util.Read;
import umicollapse.util.Utils;

public class TestBitSet{
    public static void main(String[] args){
        test("ATCG", "ATCN", 1);
        test("ATCG", "ATCG", 0);
        test("ATCG", "AGCC", 2);
        test("ANCG", "ANCC", 1);

        BitSet mutable = Utils.toBitSet("N");
        mutable.setEncodedBase(0, Read.ENCODING_MAP.get('A'));
        test(mutable, Utils.toBitSet("A"), 0, "reset N to A");

        mutable.setEncodedBase(0, Read.ENCODING_MAP.get('T'));
        test(mutable, Utils.toBitSet("T"), 0, "reset A to T");

        System.out.println("Passed: BitSet distance and encoded-base mutation tests");
    }

    private static void test(String a, String b, int expected){
        BitSet aa = Utils.toBitSet(a);
        BitSet bb = Utils.toBitSet(b);
        test(aa, bb, expected, a + " versus " + b);
    }

    private static void test(BitSet a, BitSet b, int expected, String description){
        int actual = Utils.umiDist(a, b);

        if(actual != expected)
            throw new AssertionError(description + ": expected distance " + expected + " but got " + actual);
    }
}
