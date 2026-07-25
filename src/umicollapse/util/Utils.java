package umicollapse.util;

public class Utils{
    public static final int HASH_CONST = 31;

    // fast Hamming distance by using pairwise equidistant encodings for each nucleotide
    public static int umiDist(BitSet a, BitSet b){
        // divide by the pairwise Hamming distance in the encoding
        return a.bitCountXOR(b) / Read.ENCODING_DIST;
    }

    public static boolean charEquals(BitSet a, int idx, int b){
        for(int i = 0; i < Read.ENCODING_LENGTH; i++){
            if(a.get(idx * Read.ENCODING_LENGTH + i) != ((b & (1 << i)) != 0))
                return false;
        }

        return true;
    }

    public static BitSet charSet(BitSet a, int idx, int b){
        /*
         * Generated keys must preserve the same N metadata as keys produced
         * by toBitSet.  setEncodedBase is the authoritative base mutation:
         * it marks undetermined bases and clears any stale N mask when a
         * recursion branch reuses this position for an ordinary base.
         */
        a.setEncodedBase(idx, b);

        return a;
    }

    public static int charGet(BitSet a, int idx){
        int res = 0;

        for(int i = 0; i < Read.ENCODING_LENGTH; i++){
            if(a.get(idx * Read.ENCODING_LENGTH + i))
                res |= 1 << i;
        }

        return res;
    }

    public static BitSet toBitSet(String s){
        return toBitSet(s, 0, s.length());
    }

    public static BitSet toBitSet(CharSequence s, int start, int end){
        BitSet res = new BitSet((end - start) * Read.ENCODING_LENGTH);

        for(int i = start; i < end; i++){
            int value = encodeBase(s.charAt(i));
            int idx = i - start;
            res.setEncodedBase(idx, value);
        }

        return res;
    }

    public static boolean isUMIBase(char c){
        switch(c){
            case 'A':
            case 'a':
            case 'T':
            case 't':
            case 'C':
            case 'c':
            case 'G':
            case 'g':
            case 'N':
            case 'n':
                return true;
            default:
                return false;
        }
    }

    public static int encodeBase(char c){
        switch(c){
            case 'A':
            case 'a':
                return 0b000;
            case 'T':
            case 't':
                return 0b101;
            case 'C':
            case 'c':
                return 0b110;
            case 'G':
            case 'g':
                return 0b011;
            case 'N':
            case 'n':
                return Read.UNDETERMINED;
            default:
                throw new IllegalArgumentException("Invalid UMI base: " + c);
        }
    }

    public static String toString(BitSet a, int length){
        char[] res = new char[length];

        for(int i = 0; i < length; i++)
            res[i] = Read.ALPHABET[Read.ENCODING_IDX.get(charGet(a, i))];

        return new String(res);
    }

    // converts quality string to byte array, using the Phred+33 format
    public static byte[] toPhred33ByteArray(String q){
        byte[] res = new byte[q.length()];

        for(int i = 0; i < q.length(); i++)
            res[i] = (byte)(q.charAt(i) - '!');

        return res;
    }

    // converts byte array to quality string, using the Phred+33 format
    public static String toPhred33String(byte[] q){
        char[] res = new char[q.length];

        for(int i = 0; i < q.length; i++)
            res[i] = (char)(q[i] + '!');

        return new String(res);
    }
}
