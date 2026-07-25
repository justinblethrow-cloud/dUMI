package umicollapse.algo;

import java.util.Map;
import java.util.HashMap;
import java.util.List;
import java.util.Set;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.ArrayDeque;
import java.util.Deque;

import umicollapse.util.BitSet;
import umicollapse.data.DataStructure;
import umicollapse.util.Read;
import umicollapse.util.ReadFreq;
import umicollapse.util.UmiFreq;
import umicollapse.util.ClusterTracker;

public class Directional implements Algorithm{
    @Override
    public List<Read> apply(Map<BitSet, ReadFreq> reads, DataStructure data, ClusterTracker tracker, int umiLength, int k, float percentage){
        if(reads.size() == 1){
            Map.Entry<BitSet, ReadFreq> only = reads.entrySet().iterator().next();
            Map<BitSet, Integer> frequencies = new HashMap<>(2);
            frequencies.put(only.getKey(), only.getValue().freq);
            data.init(frequencies, umiLength, k);
            data.removeNear(only.getKey(), k, Integer.MAX_VALUE);

            if(tracker.shouldTrack()){
                tracker.addAll(Collections.singleton(only.getKey()), reads);
                tracker.track(only.getKey(), only.getValue().read);
            }

            return Collections.singletonList(only.getValue().read);
        }

        UmiFreq[] freq = new UmiFreq[reads.size()];
        List<Read> res = new ArrayList<>(reads.size());
        Map<BitSet, Integer> m = new HashMap<>(hashMapCapacity(reads.size()));
        int idx = 0;

        for(Map.Entry<BitSet, ReadFreq> e : reads.entrySet()){
            freq[idx] = new UmiFreq(e.getKey(), e.getValue());
            m.put(e.getKey(), e.getValue().freq);
            idx++;
        }

        Arrays.sort(freq, (a, b) -> {
            int frequencyOrder = Integer.compare(b.readFreq.freq, a.readFreq.freq);

            if(frequencyOrder != 0)
                return frequencyOrder;

            // Equal-frequency UMIs can compete for the same neighboring UMI.
            // HashMap iteration order is not a stable scientific tie-breaker.
            return a.umi.compareTo(b.umi);
        });
        data.init(m, umiLength, k);

        for(int i = 0; i < freq.length; i++){
            if(data.contains(freq[i].umi)){
                visitAndRemove(freq[i].umi, reads, data, tracker, k, percentage);
                tracker.track(freq[i].umi, freq[i].readFreq.read);
                res.add(freq[i].readFreq.read);
            }
        }

        return res;
    }

    private static int hashMapCapacity(int expectedSize){
        if(expectedSize < 3)
            return 4;

        long capacity = ((long)expectedSize * 4L) / 3L + 1L;
        return capacity > (1 << 30) ? (1 << 30) : (int)capacity;
    }

    static int directionalThreshold(int frequency, float percentage){
        long incrementedFrequency = (long)frequency + 1L;
        float threshold = percentage * incrementedFrequency;

        if(threshold >= Integer.MAX_VALUE)
            return Integer.MAX_VALUE;
        if(threshold <= Integer.MIN_VALUE)
            return Integer.MIN_VALUE;

        // Preserve the historical truncation toward zero for normal values.
        return (int)threshold;
    }

    private void visitAndRemove(BitSet u, Map<BitSet, ReadFreq> reads, DataStructure data, ClusterTracker tracker, int k, float percentage){
        Deque<BitSet> pending = new ArrayDeque<>();
        Deque<BitSet> children = new ArrayDeque<>();
        pending.push(u);

        while(!pending.isEmpty()){
            BitSet current = pending.pop();
            Set<BitSet> c = data.removeNear(
                current,
                k,
                directionalThreshold(reads.get(current).freq, percentage)
            );
            tracker.addAll(c, reads);

            /*
             * removeNear removes every returned UMI before it is scheduled,
             * so the data structure itself is the visited set. Buffering the
             * children in iteration order and pushing them in reverse retains
             * the recursive depth-first visitation order.
             */
            for(BitSet v : c){
                if(!current.equals(v))
                    children.addLast(v);
            }

            while(!children.isEmpty())
                pending.push(children.removeLast());
        }
    }
}
