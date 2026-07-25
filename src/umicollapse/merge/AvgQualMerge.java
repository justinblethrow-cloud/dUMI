package umicollapse.merge;

import umicollapse.util.FASTQRead;
import umicollapse.util.Read;
import umicollapse.util.SAMRead;

public class AvgQualMerge implements Merge{
    @Override
    public Read merge(Read a, Read b){
        int qualityOrder = Integer.compare(a.getAvgQual(), b.getAvgQual());

        if(qualityOrder > 0)
            return a;
        else if(qualityOrder < 0)
            return b;

        if(a instanceof SAMRead && b instanceof SAMRead)
            return ((SAMRead)a).compareForTieBreak((SAMRead)b) <= 0 ? a : b;

        if(a instanceof FASTQRead && b instanceof FASTQRead)
            return ((FASTQRead)a).compareForTieBreak((FASTQRead)b) <= 0 ? a : b;

        throw new IllegalArgumentException(
            "Cannot deterministically break an average-quality tie between " +
            a.getClass().getName() + " and " + b.getClass().getName()
        );
    }
}
