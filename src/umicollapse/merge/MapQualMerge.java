package umicollapse.merge;

import umicollapse.util.Read;
import umicollapse.util.SAMRead;

public class MapQualMerge implements Merge{
    @Override
    public Read merge(Read a, Read b){
        SAMRead samA = (SAMRead)a;
        SAMRead samB = (SAMRead)b;
        int mapQualityOrder = Integer.compare(samA.getMapQual(), samB.getMapQual());

        if(mapQualityOrder > 0)
            return a;
        else if(mapQualityOrder < 0)
            return b;

        // Coordinate sorting does not define record order within a coordinate.
        // Break equal-MAPQ ties from stable record content.
        return samA.compareForTieBreak(samB) <= 0 ? a : b;
    }
}
