package umicollapse.data;

import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.HashMap;
import java.util.ArrayDeque;
import java.util.Deque;

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
        if(umiLength <= 0)
            throw new IllegalArgumentException("UMI length must be positive");

        if(maxEdits < 0 || maxEdits >= umiLength)
            throw new IllegalArgumentException(
                "Maximum edits must satisfy 0 <= maxEdits < UMI length ("
                + umiLength + "): " + maxEdits
            );

        this.umiFreq = umiFreq;
        this.umiLength = umiLength;
        this.maxEdits = maxEdits;
        ngramSize = umiLength / (maxEdits + 1);

        useLongIntervalKeys = canUseLongIntervalKeys(umiLength, ngramSize, maxEdits);

        if(useLongIntervalKeys){
            m = null;
            longMap = new LongNodeMap(expectedPackedNgramEntries(
                umiFreq.size(),
                umiLength,
                ngramSize,
                maxEdits
            ));
        }else{
            longMap = null;
            m = new HashMap<Interval, Node>(expectedNgramMapCapacity(umiFreq.size(), maxEdits));
        }

        for(Map.Entry<BitSet, Integer> e : umiFreq.entrySet())
            insert(e.getKey(), e.getValue());
    }

    // The pigeonhole lookup requires k <= the configured maximum edit count.
    @Override
    public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
        if(k < 0 || k > maxEdits)
            throw new IllegalArgumentException(
                "Requested edit distance must satisfy 0 <= k <= maxEdits ("
                + maxEdits + "): " + k
            );

        Set<BitSet> res = new HashSet<>();
        boolean queryRemoved = maxFreq == Integer.MAX_VALUE || !umiFreq.containsKey(umi);

        for(int i = 0; i < maxEdits + 1; i++){
            int lo = i * ngramSize;
            int hi = i == maxEdits ? (umiLength - 1) : ((i + 1) * ngramSize - 1);
            Node curr = useLongIntervalKeys ? longMap.get(intervalKey(umi, lo, hi)) : m.get(new Interval(umi, lo, hi));

            if(curr != null){
                if(!queryRemoved){ // always remove the queried UMI
                    removeNearBKTreeIterative(umi, curr, 0, Integer.MAX_VALUE, res);
                    queryRemoved = !umiFreq.containsKey(umi);
                }

                removeNearBKTreeIterative(umi, curr, k, maxFreq, res);
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
        long expectedEntries = (long)umiCount * ((long)maxEdits + 1L);
        return expectedEntries > Integer.MAX_VALUE ? Integer.MAX_VALUE : (int)expectedEntries;
    }

    private static int expectedNgramMapCapacity(int umiCount, int maxEdits){
        long expectedEntries = expectedNgramEntries(umiCount, maxEdits);
        long capacity = (expectedEntries * 4) / 3 + 1;

        if(capacity < 16)
            return 16;

        return capacity > (1 << 30) ? (1 << 30) : (int)capacity;
    }

    private static int expectedPackedNgramEntries(
        int umiCount,
        int umiLength,
        int ngramSize,
        int maxEdits
    ){
        if(umiCount <= 0)
            return 0;

        /*
         * A UMI contributes at most one key per interval, but an interval of
         * length n has only five^n valid sequence keys.  The packed endpoint
         * fields make different non-empty intervals disjoint.  Capping the
         * estimate by that key universe avoids allocating for impossible
         * entries when many UMIs share short n-grams.
         *
         * LongNodeMap still resizes normally, so this remains safe for callers
         * that construct BitSets outside the five-base UMI input domain.
         */
        long regularUniverse = cappedSequenceUniverse(ngramSize, umiCount);
        long regularEntries = saturatedMultiply(regularUniverse, maxEdits, Integer.MAX_VALUE);
        long lastLength = (long)umiLength - (long)maxEdits * ngramSize;
        long lastUniverse = cappedSequenceUniverse((int)lastLength, umiCount);

        return (int)saturatedAdd(regularEntries, lastUniverse, Integer.MAX_VALUE);
    }

    private static long cappedSequenceUniverse(int length, int cap){
        if(length < 0)
            throw new IllegalArgumentException("N-gram interval length cannot be negative");

        long universe = 1L;
        int alphabetSize = Read.ALPHABET.length;

        for(int i = 0; i < length; i++){
            if(universe > cap / alphabetSize)
                return cap;

            universe *= alphabetSize;
        }

        return Math.min(universe, (long)cap);
    }

    private static long saturatedMultiply(long a, long b, long cap){
        if(a == 0L || b == 0L)
            return 0L;

        return a > cap / b ? cap : a * b;
    }

    private static long saturatedAdd(long a, long b, long cap){
        return a > cap - b ? cap : a + b;
    }

    private static boolean canUseLongIntervalKeys(int umiLength, int ngramSize, int maxEdits){
        if(umiLength < 0 || umiLength > 255 || ngramSize < 0 || maxEdits < 0)
            return false;

        long lastLength = (long)umiLength - (long)maxEdits * ngramSize;
        if(lastLength < 0)
            return false;

        long maxIntervalLength = Math.max((long)ngramSize, lastLength);
        return maxIntervalLength <= 16;
    }

    private static long intervalKey(BitSet s, int lo, int hi){
        long seq = 0L;

        for(int i = lo; i <= hi; i++)
            seq = (seq << Read.ENCODING_LENGTH) | charGet(s, i);

        return (((long)lo & 0xffL) << 56) | (((long)hi & 0xffL) << 48) | seq;
    }

    private void removeNearBKTreeIterative(
            BitSet umi,
            Node start,
            int k,
            int maxFreq,
            Set<BitSet> res){
        Deque<RemovalFrame> stack = new ArrayDeque<>();
        stack.push(new RemovalFrame(start));

        while(!stack.isEmpty()){
            RemovalFrame frame = stack.peek();

            if(!frame.entered){
                int dist = umiDist(umi, frame.node.getUMI());
                boolean exists = umiFreq.containsKey(frame.node.getUMI());

                if(dist <= k && exists && frame.node.getFreq() <= maxFreq){
                    res.add(frame.node.getUMI());
                    umiFreq.remove(frame.node.getUMI());
                    exists = false;
                }

                frame.subtreeExists = exists;
                frame.minFreq = exists
                    ? frame.node.getFreq()
                    : Integer.MAX_VALUE;
                frame.lo = Math.max(dist - k, 0);
                frame.childCount = frame.node.hasNodes()
                    ? frame.node.getNodeCount()
                    : 0;
                frame.hi = Math.min(dist + k, frame.childCount - 1);
                frame.entered = true;
            }

            boolean descended = false;

            while(frame.nextChild < frame.childCount){
                int childIndex = frame.nextChild++;

                if(!frame.node.subtreeExists(childIndex))
                    continue;

                if(childIndex >= frame.lo
                        && childIndex <= frame.hi
                        && frame.node.minFreq(childIndex) <= maxFreq){
                    stack.push(new RemovalFrame(frame.node.get(childIndex)));
                    descended = true;
                    break;
                }

                frame.minFreq = Math.min(frame.minFreq, frame.node.minFreq(childIndex));
                frame.subtreeExists |= frame.node.subtreeExists(childIndex);
            }

            if(descended)
                continue;

            frame.node.setSubtreeExists(frame.subtreeExists);
            frame.node.setMinFreq(frame.minFreq);
            stack.pop();

            if(!stack.isEmpty()){
                RemovalFrame parent = stack.peek();
                parent.minFreq = Math.min(parent.minFreq, frame.minFreq);
                parent.subtreeExists |= frame.subtreeExists;
            }
        }
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
            threshold = (int)((long)capacity * 2L / 3L);
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
            if(keys.length >= (1 << 30))
                throw new IllegalStateException("NgramBKTree packed-key map exceeded its maximum capacity");

            long[] oldKeys = keys;
            Node[] oldValues = values;
            int newCapacity = keys.length << 1;

            keys = new long[newCapacity];
            values = new Node[newCapacity];
            mask = newCapacity - 1;
            threshold = (int)((long)newCapacity * 2L / 3L);
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

    private static class RemovalFrame{
        private final Node node;
        private boolean entered, subtreeExists;
        private int lo, hi, nextChild, childCount, minFreq;

        RemovalFrame(Node node){
            this.node = node;
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

    private static class Interval implements Comparable<Interval>{
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
        public int compareTo(Interval other){

            if(lo != other.lo)
                return Integer.compare(lo, other.lo);

            if(hi != other.hi)
                return Integer.compare(hi, other.hi);

            for(int i = 0; i < hi - lo + 1; i++){
                int a = get(i);
                int b = other.get(i);

                if(a != b)
                    return Integer.compare(a, b);
            }

            return 0;
        }
    }
}
