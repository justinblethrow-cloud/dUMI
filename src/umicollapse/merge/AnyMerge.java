package umicollapse.merge;

import umicollapse.util.Read;

public class AnyMerge implements Merge{
    @Override
    public Read merge(Read a, Read b){
        // Deliberately preserve the historical arbitrary/encounter-order policy.
        // Use avgqual or mapqual when representative content must be stable.
        return a;
    }
}
