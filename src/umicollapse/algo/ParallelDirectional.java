package umicollapse.algo;

import java.util.Map;
import java.util.HashMap;
import java.util.Set;
import java.util.HashSet;
import java.util.List;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.stream.IntStream;

import umicollapse.util.BitSet;
import umicollapse.data.ParallelDataStructure;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.UmiFreq;
import umicollapse.util.ClusterTracker;

public class ParallelDirectional implements ParallelAlgorithm{
    @Override
    public List<Read> apply(Map<BitSet, ReadFreq> reads, ParallelDataStructure data, ClusterTracker tracker, int umiLength, int k, float percentage){
        if(tracker.shouldTrack()){
            throw new UnsupportedOperationException();
        }

        UmiFreq[] freq = new UmiFreq[reads.size()];
        List<Read> res = new ArrayList<>();
        Map<BitSet, Integer> m = new HashMap<>();
        int idx = 0;

        for(Map.Entry<BitSet, ReadFreq> e : reads.entrySet()){
            freq[idx] = new UmiFreq(e.getKey(), e.getValue());
            m.put(e.getKey(), e.getValue().freq);
            idx++;
        }

        Arrays.parallelSort(freq, (a, b) -> {
            int frequencyOrder = Integer.compare(b.readFreq.freq, a.readFreq.freq);

            if(frequencyOrder != 0)
                return frequencyOrder;

            return a.umi.compareTo(b.umi);
        });
        data.init(m, umiLength, k);

        List<Set<BitSet>> adjIdx = new ArrayList<>();

        for(int i = 0; i < freq.length; i++)
            adjIdx.add(null);

        IntStream.range(0, freq.length).parallel()
            .forEach(i -> adjIdx.set(
                i,
                data.near(
                    freq[i].umi,
                    k,
                    Directional.directionalThreshold(freq[i].readFreq.freq, percentage)
                )
            ));

        Map<BitSet, Set<BitSet>> adj = new HashMap<>();

        for(int i = 0; i < freq.length; i++)
            adj.put(freq[i].umi, adjIdx.get(i));

        Set<BitSet> visited = new HashSet<>();

        for(int i = 0; i < freq.length; i++){
            if(!visited.contains(freq[i].umi)){
                visitAndRemove(freq[i].umi, adj, visited);
                res.add(freq[i].readFreq.read);
            }
        }

        return res;
    }

    private void visitAndRemove(BitSet u, Map<BitSet, Set<BitSet>> adj, Set<BitSet> visited){
        Deque<BitSet> pending = new ArrayDeque<>();
        Deque<BitSet> children = new ArrayDeque<>();
        // Discovery is query-local: mark on enqueue so dense graphs cannot
        // accumulate duplicate pending entries before a node is visited.
        Set<BitSet> scheduled = new HashSet<>();
        scheduled.add(u);
        pending.push(u);

        while(!pending.isEmpty()){
            BitSet current = pending.pop();

            if(!visited.add(current))
                continue;

            for(BitSet v : adj.get(current)){
                if(!current.equals(v) && !visited.contains(v) && scheduled.add(v))
                    children.addLast(v);
            }

            while(!children.isEmpty())
                pending.push(children.removeLast());
        }
    }
}
