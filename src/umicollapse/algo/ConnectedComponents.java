package umicollapse.algo;

import java.util.List;
import java.util.ArrayList;
import java.util.Map;
import java.util.HashMap;
import java.util.Set;
import java.util.Collections;
import java.util.ArrayDeque;
import java.util.Deque;

import umicollapse.util.BitSet;
import umicollapse.data.DataStructure;
import umicollapse.util.ReadFreq;
import umicollapse.util.UmiFreq;
import umicollapse.util.Read;
import umicollapse.util.ClusterTracker;

public class ConnectedComponents implements Algorithm{
    @Override
    public List<Read> apply(Map<BitSet, ReadFreq> reads, DataStructure data, ClusterTracker tracker, int umiLength, int k, float percentage){
        Map<BitSet, Integer> m = new HashMap<>();

        for(Map.Entry<BitSet, ReadFreq> e : reads.entrySet())
            m.put(e.getKey(), e.getValue().freq);

        data.init(m, umiLength, k);
        List<Read> res = new ArrayList<>();
        List<BitSet> sortedUmis = new ArrayList<>(reads.keySet());
        Collections.sort(sortedUmis);

        for(BitSet umi : sortedUmis){
            if(data.contains(umi)){
                UmiFreq umiFreq = visitAndRemove(umi, reads, data, tracker, k);
                tracker.track(umiFreq.umi, umiFreq.readFreq.read);
                res.add(umiFreq.readFreq.read);
            }
        }

        return res;
    }

    private UmiFreq visitAndRemove(BitSet u, Map<BitSet, ReadFreq> reads, DataStructure data, ClusterTracker tracker, int k){
        UmiFreq max = new UmiFreq(u, reads.get(u));
        Deque<BitSet> pending = new ArrayDeque<>();
        Deque<BitSet> children = new ArrayDeque<>();
        pending.push(u);

        while(!pending.isEmpty()){
            BitSet current = pending.pop();
            Set<BitSet> c = data.removeNear(current, k, Integer.MAX_VALUE);
            tracker.addAll(c, reads);

            for(BitSet v : c){
                ReadFreq candidate = reads.get(v);
                int frequencyOrder = Integer.compare(candidate.freq, max.readFreq.freq);

                if(frequencyOrder > 0 || (frequencyOrder == 0 && v.compareTo(max.umi) < 0))
                    max = new UmiFreq(v, candidate);

                if(!current.equals(v))
                    children.addLast(v);
            }

            // As above, removal supplies visited-state and prevents rescheduling.
            while(!children.isEmpty())
                pending.push(children.removeLast());
        }

        return max;
    }
}
