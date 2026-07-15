package umicollapse.merge;

import umicollapse.util.Read;
import umicollapse.util.SAMRead;

public class MapQualMerge implements Merge{
    @Override
    public Read merge(Read a, Read b){
        SAMRead samA = (SAMRead)a;
        SAMRead samB = (SAMRead)b;

        if(samA.getMapQual() > samB.getMapQual())
            return a;
        else if(samA.getMapQual() < samB.getMapQual())
            return b;

        // Coordinate-sorted BAM does not define the order of records sharing a
        // coordinate. Resolve mapping-quality ties from record content so the
        // representative does not depend on mapper thread scheduling.
        int nameOrder = samA.toSAMRecord().getReadName().compareTo(
                samB.toSAMRecord().getReadName()
        );

        if(nameOrder < 0)
            return a;
        else if(nameOrder > 0)
            return b;

        return samA.toSAMRecord().getSAMString().compareTo(
                samB.toSAMRecord().getSAMString()
        ) <= 0 ? a : b;
    }
}
