package umicollapse.util;

import java.util.Arrays;
import java.util.Objects;

import htsjdk.samtools.fastq.FastqRecord;

import static umicollapse.util.Utils.toBitSet;
import static umicollapse.util.Utils.toPhred33ByteArray;
import static umicollapse.util.Utils.toPhred33String;

public class FASTQRead extends Read{
    private String desc;
    private BitSet seq;
    private byte[] qual;
    private int avgQual;

    public FASTQRead(String desc, String umi, String seq, String qual){
        this.desc = desc;
        this.seq = toBitSet(umi.toUpperCase() + seq.toUpperCase());
        this.qual = toPhred33ByteArray(qual);

        float avg = 0.0f;

        for(byte b : this.qual)
            avg += b;

        this.avgQual = (int)(avg / this.qual.length);
    }

    public FASTQRead(String desc, String umiAndSeq, String qual){
        this.desc = desc;
        this.seq = toBitSet(umiAndSeq.toUpperCase());
        this.qual = toPhred33ByteArray(qual);

        float avg = 0.0f;

        for(byte b : this.qual)
            avg += b;

        this.avgQual = (int)(avg / this.qual.length);
    }

    @Override
    public BitSet getUMI(int maxLength){
        return seq;
    }

    @Override
    public int getUMILength(){
        return -1; // should never be called!
    }

    @Override
    public int getAvgQual(){
        return avgQual;
    }

    @Override
    public boolean equals(Object o){
        if(this == o)
            return true;

        if(!(o instanceof FASTQRead))
            return false;

        FASTQRead r = (FASTQRead)o;

        if(!seq.equals(r.seq))
            return false;

        if(!Objects.equals(desc, r.desc))
            return false;

        if(!Arrays.equals(qual, r.qual))
            return false;

        return true;
    }

    @Override
    public int hashCode(){
        int result = Objects.hashCode(desc);
        result = 31 * result + seq.hashCode();
        result = 31 * result + Arrays.hashCode(qual);
        return result;
    }

    public int compareForTieBreak(FASTQRead other){
        int cmp = compareNullableStrings(desc, other.desc);

        if(cmp != 0)
            return cmp;

        cmp = seq.compareTo(other.seq);

        if(cmp != 0)
            return cmp;

        int length = Math.min(qual.length, other.qual.length);

        for(int i = 0; i < length; i++){
            cmp = Integer.compare(Byte.toUnsignedInt(qual[i]), Byte.toUnsignedInt(other.qual[i]));

            if(cmp != 0)
                return cmp;
        }

        return Integer.compare(qual.length, other.qual.length);
    }

    public FastqRecord toFASTQRecord(int length, int umiLength){
        return new FastqRecord(desc, Utils.toString(seq, length).substring(umiLength), "", Utils.toPhred33String(qual).substring(umiLength));
    }

    private static int compareNullableStrings(String a, String b){
        if(a == b)
            return 0;
        if(a == null)
            return -1;
        if(b == null)
            return 1;
        return a.compareTo(b);
    }
}
