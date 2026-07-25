package umicollapse.util;

import java.util.Arrays;

public class BitSet implements Comparable<BitSet>{
    private static final int CHUNK_SIZE = 64;

    private long[] bits;
    private long[] nBits;
    private boolean recalcHash;
    private int hash;

    public BitSet(int length){
        this.bits = new long[length / CHUNK_SIZE + (length % CHUNK_SIZE == 0 ? 0 : 1)];
        this.recalcHash = true;
    }

    private BitSet(long[] bits, long[] nBits){
        this.bits = bits;
        this.nBits = nBits;
        this.recalcHash = true;
    }

    private BitSet(long[] bits, long[] nBits, int hash){
        this.bits = bits;
        this.nBits = nBits;
        this.recalcHash = false;
        this.hash = hash;
    }

    public boolean get(int idx){
        return (bits[idx / CHUNK_SIZE] & (1L << (idx % CHUNK_SIZE))) != 0L;
    }

    // does not set the nBits array, so distance calculations could be wrong if not careful!
    public void set(int idx, boolean bit){
        recalcHash = true;
        int i = idx / CHUNK_SIZE;
        int j = idx % CHUNK_SIZE;
        bits[i] = bit ? (bits[i] | (1L << j)) : (bits[i] & ~(1L << j));
    }

    public void setNBit(int idx, boolean bit){
        if(nBits == null)
            nBits = new long[bits.length];

        recalcHash = true;
        int i = idx / CHUNK_SIZE;
        int j = idx % CHUNK_SIZE;
        nBits[i] = bit ? (nBits[i] | (1L << j)) : (nBits[i] & ~(1L << j));
    }

    public void setEncodedBase(int idx, int value){
        recalcHash = true;
        int bitIdx = idx * Read.ENCODING_LENGTH;

        for(int i = 0; i < Read.ENCODING_LENGTH; i++){
            int j = bitIdx + i;
            int chunk = j / CHUNK_SIZE;
            long mask = 1L << (j % CHUNK_SIZE);

            if((value & (1 << i)) != 0)
                bits[chunk] |= mask;
            else
                bits[chunk] &= ~mask;

            if(nBits != null)
                nBits[chunk] &= ~mask;
        }

        if(value == Read.UNDETERMINED){
            if(nBits == null)
                nBits = new long[bits.length];

            for(int i = 0; i < Read.ENCODING_LENGTH; i++){
                int j = bitIdx + i;
                nBits[j / CHUNK_SIZE] |= 1L << (j % CHUNK_SIZE);
            }
        }
    }

    public int bitCountXOR(BitSet o){
        int res = 0;

        for(int i = 0; i < bits.length; i++){
            long xor = (nBits == null ? 0L : nBits[i]) ^ (o.nBits == null ? 0L : o.nBits[i]);
            res += Long.bitCount(xor | (bits[i] ^ o.bits[i])) - Long.bitCount(xor) / Read.ENCODING_LENGTH;
        }

        return res;
    }

    @Override
    public boolean equals(Object obj){
        if(!(obj instanceof BitSet))
            return false;

        BitSet o = (BitSet)obj;

        if(this == o)
            return true;

        if(bits.length != o.bits.length)
            return false;

        for(int i = 0; i < bits.length; i++){
            if(bits[i] != o.bits[i] || nBitsAt(i) != o.nBitsAt(i))
                return false;
        }

        return true;
    }

    @Override
    public int compareTo(BitSet other){
        if(bits.length != other.bits.length)
            return Integer.compare(bits.length, other.bits.length);

        for(int i = 0; i < bits.length; i++){
            if(bits[i] != other.bits[i])
                return Long.compare(bits[i], other.bits[i]);
        }

        // nBits is normally derivable from the encoded bases, but it remains
        // part of distance semantics and must therefore participate in the
        // total order just as it does in equality.
        for(int i = 0; i < bits.length; i++){
            long thisNBits = nBitsAt(i);
            long otherNBits = other.nBitsAt(i);

            if(thisNBits != otherNBits)
                return Long.compare(thisNBits, otherNBits);
        }

        return 0;
    }

    @Override
    public BitSet clone(){
        long[] clonedBits = Arrays.copyOf(bits, bits.length);
        long[] clonedNBits = nBits == null ? null : Arrays.copyOf(nBits, nBits.length);

        if(recalcHash)
            return new BitSet(clonedBits, clonedNBits);
        else
            return new BitSet(clonedBits, clonedNBits, hash);
    }

    @Override
    public int hashCode(){
        if(recalcHash){
            long h = 1234L; // same as Java's built-in BitSet hash function

            for(int i = bits.length; --i >= 0;){
                h ^= bits[i] * (i + 1L);

                long nChunk = nBitsAt(i);
                if(nChunk != 0L)
                    h ^= Long.rotateLeft(nChunk, 17) * (i + 1L);
            }

            hash = (int)((h >> 32) ^ h);
            recalcHash = false;
        }

        return hash;
    }

    private long nBitsAt(int idx){
        return nBits == null ? 0L : nBits[idx];
    }

    @Override
    public String toString(){
        StringBuilder res = new StringBuilder();

        for(int i = 0; i < bits.length; i++){
            String s = Long.toBinaryString(bits[i]);
            res.append(reverse(s));
            res.append(make('0', CHUNK_SIZE - s.length()));
        }

        return res.toString();
    }

    private String make(char c, int n){
        char[] res = new char[n];

        for(int i = 0; i < n; i++)
            res[i] = c;

        return new String(res);
    }

    private String reverse(String s){
        char[] res = new char[s.length()];

        for(int i = 0; i < s.length(); i++)
            res[i] = s.charAt(s.length() - 1 - i);

        return new String(res);
    }
}
