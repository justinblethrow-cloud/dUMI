package umicollapse.data;

import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.HashMap;
import java.util.TreeMap;
import java.util.ArrayDeque;
import java.util.Deque;

import umicollapse.util.BitSet;
import static umicollapse.util.Utils.umiDist;

public class FenwickBKTree implements DataStructure{
    private Set<BitSet> s;
    private TreeMap<Integer, Integer> freqs;
    private int umiLength;
    private Node[] fenwick;

    @Override
    public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
        this.s = umiFreq.keySet();
        this.umiLength = umiLength;

        freqs = new TreeMap<Integer, Integer>();

        for(Map.Entry<BitSet, Integer> e : umiFreq.entrySet())
            freqs.put(e.getValue(), null);

        int idx = 0;

        for(Integer key : freqs.keySet())
            freqs.put(key, idx++);

        fenwick = new Node[freqs.size() + 1]; // build Fenwick tree on frequencies

        for(Map.Entry<BitSet, Integer> e : umiFreq.entrySet()){
            BitSet umi = e.getKey();
            int freq = e.getValue();
            insert(umi, freq);
        }
    }

    @Override
    public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
        Set<BitSet> res = new HashSet<>();

        if(maxFreq != Integer.MAX_VALUE){ // always remove the queried UMI
            int freqIdx = freqs.size();

            for(; freqIdx > 0; freqIdx -= freqIdx & (-freqIdx))
                removeNearIterative(umi, fenwick[freqIdx], 0, res);
        }

        Map.Entry<Integer, Integer> floorEntry = freqs.floorEntry(maxFreq);

        if(floorEntry == null)
            return res;

        int freqIdx = floorEntry.getValue() + 1;

        for(; freqIdx > 0; freqIdx -= freqIdx & (-freqIdx))
            removeNearIterative(umi, fenwick[freqIdx], k, res);

        return res;
    }

    private void removeNearIterative(BitSet umi, Node start, int k, Set<BitSet> res){
        Deque<RemovalFrame> stack = new ArrayDeque<>();
        stack.push(new RemovalFrame(start));

        while(!stack.isEmpty()){
            RemovalFrame frame = stack.peek();

            if(!frame.entered){
                int dist = umiDist(umi, frame.node.getUMI());
                boolean exists = s.contains(frame.node.getUMI());

                if(dist <= k && exists){
                    res.add(frame.node.getUMI());
                    s.remove(frame.node.getUMI());
                    exists = false;
                }

                frame.subtreeExists = exists;
                frame.lo = Math.max(dist - k, 0);
                frame.hi = Math.min(dist + k, umiLength);
                frame.childCount = frame.node.hasNodes() ? umiLength + 1 : 0;
                frame.entered = true;
            }

            boolean descended = false;

            while(frame.nextChild < frame.childCount){
                int childIndex = frame.nextChild++;

                if(!frame.node.subtreeExists(childIndex))
                    continue;

                if(childIndex >= frame.lo && childIndex <= frame.hi){
                    stack.push(new RemovalFrame(frame.node.get(childIndex)));
                    descended = true;
                    break;
                }

                frame.subtreeExists = true;
            }

            if(descended)
                continue;

            frame.node.setSubtreeExists(frame.subtreeExists);
            stack.pop();

            if(!stack.isEmpty())
                stack.peek().subtreeExists |= frame.subtreeExists;
        }
    }

    private void insert(BitSet umi, int freq){
        int freqIdx = freqs.get(freq) + 1;

        for(; freqIdx <= freqs.size(); freqIdx += freqIdx & (-freqIdx)){
            if(fenwick[freqIdx] == null){
                fenwick[freqIdx] = new Node(umi);
            }else{
                Node curr = fenwick[freqIdx];
                int dist;

                do{
                    dist = umiDist(umi, curr.getUMI());
                }while((curr = curr.initNode(dist, umi, umiLength)) != null);
            }
        }
    }

    @Override
    public boolean contains(BitSet umi){
        return s.contains(umi);
    }

    @Override
    public Map<String, Float> stats(){
        Map<String, Float> res = new HashMap<>();

        double[] d = new double[3];

        for(Node curr : fenwick){
            if(curr != null){
                double[] a = depth(curr);
                d[0] += a[0];
                d[1] = Math.max(d[1], a[1]);
                d[2] += a[2];
            }
        }

        res.put("max depth", (float)d[1]);
        res.put("avg depth", (float)(d[2] / d[0]));
        return res;
    }

    private double[] depth(Node start){
        double[] result = new double[3]; // num leaf nodes, max depth, depth sum
        Deque<DepthFrame> pending = new ArrayDeque<>();
        pending.push(new DepthFrame(start, 1));

        while(!pending.isEmpty()){
            DepthFrame frame = pending.pop();
            boolean isLeaf = true;

            for(int i = umiLength; i >= 0; i--){
                if(frame.node.hasNode(i)){
                    pending.push(new DepthFrame(frame.node.get(i), frame.depth + 1));
                    isLeaf = false;
                }
            }

            if(isLeaf){
                result[0] += 1;
                result[1] = Math.max(result[1], frame.depth);
                result[2] += frame.depth;
            }
        }

        return result;
    }

    private static class DepthFrame{
        private final Node node;
        private final int depth;

        DepthFrame(Node node, int depth){
            this.node = node;
            this.depth = depth;
        }
    }

    private static class RemovalFrame{
        private final Node node;
        private boolean entered, subtreeExists;
        private int lo, hi, nextChild, childCount;

        RemovalFrame(Node node){
            this.node = node;
        }
    }

    private static class Node{
        private BitSet umi;
        private boolean subtreeExists;
        private Node[] c;

        Node(BitSet umi){
            this.c = null;
            this.umi = umi;
            this.subtreeExists = true;
        }

        Node initNode(int k, BitSet umi, int umiLength){
            if(c == null)
                c = new Node[umiLength + 1];

            if(c[k] == null){
                c[k] = new Node(umi);
                return null;
            }

            return c[k];
        }

        BitSet getUMI(){
            return umi;
        }

        void setSubtreeExists(boolean subtreeExists){
            this.subtreeExists = subtreeExists;
        }

        boolean subtreeExists(int k){
            return c[k] != null && c[k].subtreeExists;
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
}
