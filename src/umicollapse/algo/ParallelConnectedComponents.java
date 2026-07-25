package umicollapse.algo;

import java.util.List;
import java.util.ArrayList;
import java.util.Set;
import java.util.HashSet;
import java.util.Map;
import java.util.HashMap;
import java.util.Collections;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.stream.IntStream;

import umicollapse.util.BitSet;
import umicollapse.data.ParallelDataStructure;
import umicollapse.util.ReadFreq;
import umicollapse.util.UmiFreq;
import umicollapse.util.Read;
import umicollapse.util.ClusterTracker;

public class ParallelConnectedComponents implements ParallelAlgorithm{
    @Override
    public List<Read> apply(Map<BitSet, ReadFreq> reads, ParallelDataStructure data, ClusterTracker tracker, int umiLength, int k, float percentage){
        if(tracker.shouldTrack()){
            throw new UnsupportedOperationException();
        }

        Map<BitSet, Integer> m = new HashMap<>();
        BitSet[] idxToUMI = new BitSet[reads.size()];
        List<BitSet> sortedUmis = new ArrayList<>(reads.keySet());
        Collections.sort(sortedUmis);

        int idx = 0;

        for(BitSet umi : sortedUmis){
            m.put(umi, reads.get(umi).freq);
            idxToUMI[idx++] = umi;
        }

        data.init(m, umiLength, k);

        List<Set<BitSet>> adjIdx = new ArrayList<>();

        for(int i = 0; i < reads.size(); i++)
            adjIdx.add(null);

        IntStream.range(0, reads.size()).parallel()
            .forEach(i -> adjIdx.set(i, data.near(idxToUMI[i], k, Integer.MAX_VALUE)));

        Map<BitSet, Set<BitSet>> adj = new HashMap<>();

        for(int i = 0; i < adjIdx.size(); i++)
            adj.put(idxToUMI[i], adjIdx.get(i));

        List<Read> res = new ArrayList<>();
        Set<BitSet> visited = new HashSet<>();

        for(BitSet umi : sortedUmis){
            if(!visited.contains(umi))
                res.add(visitAndRemove(umi, reads, adj, visited).readFreq.read);
        }

        return res;
    }

    private UmiFreq visitAndRemove(BitSet u, Map<BitSet, ReadFreq> reads, Map<BitSet, Set<BitSet>> adj, Set<BitSet> visited){
        UmiFreq max = new UmiFreq(u, reads.get(u));
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

            ReadFreq candidate = reads.get(current);
            int frequencyOrder = Integer.compare(candidate.freq, max.readFreq.freq);

            if(frequencyOrder > 0 || (frequencyOrder == 0 && current.compareTo(max.umi) < 0))
                max = new UmiFreq(current, candidate);

            for(BitSet v : adj.get(current)){
                if(!current.equals(v) && !visited.contains(v) && scheduled.add(v))
                    children.addLast(v);
            }

            while(!children.isEmpty())
                pending.push(children.removeLast());
        }

        return max;
    }
}
