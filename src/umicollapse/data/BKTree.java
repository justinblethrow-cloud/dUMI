package umicollapse.data;

import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.HashMap;
import java.util.ArrayDeque;
import java.util.Deque;

import umicollapse.util.BitSet;
import static umicollapse.util.Utils.umiDist;

public class BKTree implements DataStructure{
    private Set<BitSet> s;
    private int umiLength;
    private Node root;

    @Override
    public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
        this.s = umiFreq.keySet();
        this.umiLength = umiLength;

        boolean first = true;

        for(Map.Entry<BitSet, Integer> e : umiFreq.entrySet()){
            BitSet umi = e.getKey();
            int freq = e.getValue();

            if(first){
                root = new Node(umi, freq);
                first = false;
            }else{
                insert(umi, freq);
            }
        }
    }

    @Override
    public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
        Set<BitSet> res = new HashSet<>();

        if(maxFreq != Integer.MAX_VALUE) // always remove the queried UMI
            removeNearIterative(umi, root, 0, Integer.MAX_VALUE, res);

        removeNearIterative(umi, root, k, maxFreq, res);
        return res;
    }

    private void removeNearIterative(BitSet umi, Node start, int k, int maxFreq, Set<BitSet> res){
        Deque<RemovalFrame> stack = new ArrayDeque<>();
        stack.push(new RemovalFrame(start));

        while(!stack.isEmpty()){
            RemovalFrame frame = stack.peek();

            if(!frame.entered){
                int dist = umiDist(umi, frame.node.getUMI());

                if(dist <= k && frame.node.exists() && frame.node.getFreq() <= maxFreq){
                    res.add(frame.node.getUMI());
                    frame.node.setExists(false);
                    s.remove(frame.node.getUMI());
                }

                frame.subtreeExists = frame.node.exists();
                frame.minFreq = frame.node.exists()
                    ? frame.node.getFreq()
                    : Integer.MAX_VALUE;
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

    private void insert(BitSet umi, int freq){
        Node curr = root;
        int dist;

        do{
            dist = umiDist(umi, curr.getUMI());
            curr.setMinFreq(Math.min(curr.getMinFreq(), freq));
        }while((curr = curr.initNode(dist, umi, umiLength, freq)) != null);
    }

    @Override
    public boolean contains(BitSet umi){
        return s.contains(umi);
    }

    @Override
    public Map<String, Float> stats(){
        Map<String, Float> res = new HashMap<>();
        double[] d = depth(root);
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
        private int lo, hi, nextChild, childCount, minFreq;

        RemovalFrame(Node node){
            this.node = node;
        }
    }

    private static class Node{
        private BitSet umi;
        private boolean exists, subtreeExists;
        private Node[] c;
        private int freq, minFreq;

        Node(BitSet umi, int freq){
            this.c = null;
            this.umi = umi;
            this.exists = true;
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

        boolean exists(){
            return exists;
        }

        void setExists(boolean exists){
            this.exists = exists;
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
}
