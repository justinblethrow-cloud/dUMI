package umicollapse.data;

import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.ArrayDeque;
import java.util.Deque;

import umicollapse.util.BitSet;
import static umicollapse.util.Utils.umiDist;

public class ParallelBKTree implements ParallelDataStructure{
    private int umiLength;
    private Node root;

    @Override
    public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
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
    public Set<BitSet> near(BitSet umi, int k, int maxFreq){
        Set<BitSet> res = new HashSet<>();
        res.add(umi);
        nearIterative(umi, root, k, maxFreq, res);
        return res;
    }

    private void nearIterative(BitSet umi, Node start, int k, int maxFreq, Set<BitSet> res){
        Deque<Node> stack = new ArrayDeque<>();
        stack.push(start);

        while(!stack.isEmpty()){
            Node curr = stack.pop();
            int dist = umiDist(umi, curr.getUMI());

            if(dist <= k && curr.getFreq() <= maxFreq)
                res.add(curr.getUMI());

            if(curr.hasNodes()){
                int lo = Math.max(dist - k, 0);
                int hi = Math.min(dist + k, umiLength);

                /*
                 * Push in reverse so the explicit LIFO stack preserves the
                 * recursive implementation's ascending child visitation.
                 */
                for(int i = hi; i >= lo; i--){
                    if(curr.hasNode(i) && curr.minFreq(i) <= maxFreq)
                        stack.push(curr.get(i));
                }
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

    private static class Node{
        private BitSet umi;
        private Node[] c;
        private int freq, minFreq;

        Node(BitSet umi, int freq){
            this.c = null;
            this.umi = umi;
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
