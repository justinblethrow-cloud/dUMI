package umicollapse.data;

import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.HashMap;

import umicollapse.util.BitSet;
import umicollapse.util.Read;
import static umicollapse.util.Utils.charGet;
import static umicollapse.util.Utils.HASH_CONST;
import static umicollapse.util.Utils.umiDist;

public class NgramBKTree implements DataStructure{
    private Map<BitSet, Integer> umiFreq;
    private int umiLength, ngramSize, maxEdits;
    private Map<Interval, Node> m;
    private LongNodeMap longMap;
    private boolean useLongIntervalKeys;

    @Override
    public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
        this.umiFreq = umiFreq;
        this.umiLength = umiLength;
        this.maxEdits = maxEdits;
        ngramSize = umiLength / (maxEdits + 1);

        useLongIntervalKeys = canUseLongIntervalKeys(umiLength, ngramSize, maxEdits);

        if(useLongIntervalKeys)
            longMap = new LongNodeMap(expectedNgramEntries(umiFreq.size(), maxEdits));
        else
            m = new HashMap<Interval, Node>(expectedNgramMapCapacity(umiFreq.size(), maxEdits));

        for(Map.Entry<BitSet, Integer> e : umiFreq.entrySet())
            insert(e.getKey(), e.getValue());
    }

    // k <= maxEdits must be satisfied
    @Override
    public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
        Set<BitSet> res = new HashSet<>();
        boolean queryRemoved = maxFreq == Integer.MAX_VALUE || !umiFreq.containsKey(umi);

        for(int i = 0; i < maxEdits + 1; i++){
            int lo = i * ngramSize;
            int hi = i == maxEdits ? (umiLength - 1) : ((i + 1) * ngramSize - 1);
            Node curr = useLongIntervalKeys ? longMap.get(intervalKey(umi, lo, hi)) : m.get(new Interval(umi, lo, hi));

            if(curr != null){
                if(!queryRemoved){ // always remove the queried UMI
                    recursiveRemoveNearBKTree(umi, curr, 0, Integer.MAX_VALUE, res);
                    queryRemoved = !umiFreq.containsKey(umi);
                }

                recursiveRemoveNearBKTree(umi, curr, k, maxFreq, res);
            }
        }

        return res;
    }

    private void insert(BitSet umi, int freq){
        for(int i = 0; i < maxEdits + 1; i++){
            int lo = i * ngramSize;
            int hi = i == maxEdits ? (umiLength - 1) : ((i + 1) * ngramSize - 1);
            Node curr;

            if(useLongIntervalKeys){
                long key = intervalKey(umi, lo, hi);
                curr = longMap.get(key);

                if(curr != null){
                    insertBKTree(curr, umi, umiLength - (hi - lo + 1), freq);
                }else{
                    longMap.put(key, new Node(umi, freq));
                }
            }else{
                Interval in = new Interval(umi, lo, hi);
                curr = m.get(in);

                if(curr != null){
                    insertBKTree(curr, umi, umiLength - (hi - lo + 1), freq);
                }else{
                    m.put(in, new Node(umi, freq));
                }
            }
        }
    }

    private static int expectedNgramEntries(int umiCount, int maxEdits){
        long expectedEntries = (long)umiCount * (maxEdits + 1);
        return expectedEntries > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int)expectedEntries;
    }

    private static int expectedNgramMapCapacity(int umiCount, int maxEdits){
        long expectedEntries = expectedNgramEntries(umiCount, maxEdits);
        long capacity = (expectedEntries * 4) / 3 + 1;

        if(capacity < 16)
            return 16;

        return capacity > (1 << 30) ? (1 << 30) : (int)capacity;
    }

    private static boolean canUseLongIntervalKeys(int umiLength, int ngramSize, int maxEdits){
        if(umiLength > 255)
            return false;

        int lastLength = umiLength - (maxEdits * ngramSize);
        int maxIntervalLength = Math.max(ngramSize, lastLength);
        return maxIntervalLength <= 16;
    }

    private static long intervalKey(BitSet s, int lo, int hi){
        long seq = 0L;

        for(int i = lo; i <= hi; i++)
            seq = (seq << Read.ENCODING_LENGTH) | charGet(s, i);

        return (((long)lo & 0xffL) << 56) | (((long)hi & 0xffL) << 48) | seq;
    }

    private void recursiveRemoveNearBKTree(BitSet umi, Node curr, int k, int maxFreq, Set<BitSet> res){
        int dist = umiDist(umi, curr.getUMI());
        boolean exists = umiFreq.containsKey(curr.getUMI());

        if(dist <= k && exists && curr.getFreq() <= maxFreq){
            res.add(curr.getUMI());
            umiFreq.remove(curr.getUMI());
        }

        boolean subtreeExists = exists;
        int minFreq = exists ? curr.getFreq() : Integer.MAX_VALUE;

        if(curr.hasNodes()){
            int lo = Math.max(dist - k, 0);
            int length = curr.getNodeCount();
            int hi = Math.min(dist + k, length - 1);

            for(int i = 0; i < length; i++){
                if(curr.subtreeExists(i)){
                    if(i >= lo && i <= hi && curr.minFreq(i) <= maxFreq)
                        recursiveRemoveNearBKTree(umi, curr.get(i), k, maxFreq, res);

                    minFreq = Math.min(minFreq, curr.minFreq(i));
                    subtreeExists |= curr.subtreeExists(i);
                }
            }
        }

        curr.setSubtreeExists(subtreeExists);
        curr.setMinFreq(minFreq);
    }

    private void insertBKTree(Node curr, BitSet umi, int length, int freq){
        int dist;

        do{
            dist = umiDist(umi, curr.getUMI());
            curr.setMinFreq(Math.min(curr.getMinFreq(), freq));
        }while((curr = curr.initNode(dist, umi, length, freq)) != null);
    }

    @Override
    public boolean contains(BitSet umi){
        return umiFreq.containsKey(umi);
    }

    @Override
    public Map<String, Float> stats(){
        Map<String, Float> res = new HashMap<>();
        res.put("num n-grams", (float)(useLongIntervalKeys ? longMap.size() : m.size()));
        res.put("n-grams size", (float)ngramSize);
        return res;
    }

    private static class LongNodeMap{
        private long[] keys;
        private Node[] values;
        private int size, threshold, mask;

        LongNodeMap(int expectedSize){
            int capacity = 16;
            long target = Math.max(16L, (long)expectedSize * 2L);

            while(capacity < target && capacity < (1 << 30))
                capacity <<= 1;

            keys = new long[capacity];
            values = new Node[capacity];
            mask = capacity - 1;
            threshold = capacity * 2 / 3;
        }

        Node get(long key){
            int idx = index(key);

            while(values[idx] != null){
                if(keys[idx] == key)
                    return values[idx];

                idx = (idx + 1) & mask;
            }

            return null;
        }

        void put(long key, Node value){
            if(size >= threshold)
                resize();

            putNoResize(key, value);
        }

        int size(){
            return size;
        }

        private void putNoResize(long key, Node value){
            int idx = index(key);

            while(values[idx] != null){
                if(keys[idx] == key){
                    values[idx] = value;
                    return;
                }

                idx = (idx + 1) & mask;
            }

            keys[idx] = key;
            values[idx] = value;
            size++;
        }

        private void resize(){
            long[] oldKeys = keys;
            Node[] oldValues = values;
            int newCapacity = keys.length << 1;

            keys = new long[newCapacity];
            values = new Node[newCapacity];
            mask = newCapacity - 1;
            threshold = newCapacity * 2 / 3;
            size = 0;

            for(int i = 0; i < oldValues.length; i++){
                if(oldValues[i] != null)
                    putNoResize(oldKeys[i], oldValues[i]);
            }
        }

        private int index(long key){
            long h = key;
            h ^= h >>> 33;
            h *= 0xff51afd7ed558ccdL;
            h ^= h >>> 33;
            h *= 0xc4ceb9fe1a85ec53L;
            h ^= h >>> 33;
            return ((int)h) & mask;
        }
    }

    private static class Node{
        private BitSet umi;
        private boolean subtreeExists;
        private Node[] c;
        private int freq, minFreq;

        Node(BitSet umi, int freq){
            this.c = null;
            this.umi = umi;
            this.subtreeExists = true;
            this.freq = freq;
            this.minFreq = freq;
        }

        Node initNode(int k, BitSet umi, int umiLength, int freq){
            if(c == null)
                c = new Node[umiLength + 1];

            if(c[k] == null){
                c[k] = new Node(umi, freq);
                return null;
            }

            return c[k];
        }

        BitSet getUMI(){
            return umi;
        }

        int getNodeCount(){
            return c.length;
        }

        void setSubtreeExists(boolean subtreeExists){
            this.subtreeExists = subtreeExists;
        }

        boolean subtreeExists(int k){
            return c[k] != null && c[k].subtreeExists;
        }

        void setMinFreq(int minFreq){
            this.minFreq = minFreq;
        }

        int getMinFreq(){
            return minFreq;
        }

        int getFreq(){
            return freq;
        }

        int minFreq(int k){
            return c[k] == null ? Integer.MAX_VALUE : c[k].minFreq;
        }

        Node get(int k){
            return c[k];
        }

        boolean hasNode(int k){
            return c != null && c[k] != null;
        }

        boolean hasNodes(){
            return c != null;
        }
    }

    private static class Interval implements Comparable{
        private BitSet s;
        private int lo, hi, hash;

        Interval(BitSet s, int lo, int hi){
            this.s = s;
            this.lo = lo;
            this.hi = hi;

            for(int i = 0; i < hi - lo + 1; i++)
                hash = hash * HASH_CONST + get(i);

            hash = hash * HASH_CONST + lo;
            hash = hash * HASH_CONST + hi;
        }

        int get(int i){
            return charGet(s, lo + i);
        }

        @Override
        public int hashCode(){
            return hash;
        }

        @Override
        public boolean equals(Object o){
            if(!(o instanceof Interval))
                return false;

            Interval other = (Interval)o;

            if(lo != other.lo || hi != other.hi)
                return false;

            for(int i = 0; i < hi - lo + 1; i++){
                if(get(i) != other.get(i))
                    return false;
            }

            return true;
        }

        @Override
        public int compareTo(Object o){
            Interval other = (Interval)o;

            if(lo != other.lo)
                return lo - other.lo;

            if(hi != other.hi)
                return hi - other.hi;

            for(int i = 0; i < hi - lo + 1; i++){
                int a = get(i);
                int b = other.get(i);

                if(a != b)
                    return a - b;
            }

            return 0;
        }
    }
}
