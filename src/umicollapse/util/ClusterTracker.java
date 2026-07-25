package umicollapse.util;

import java.util.List;
import java.util.ArrayList;
import java.util.Map;
import java.util.HashMap;
import java.util.Set;

public class ClusterTracker{
    private boolean track;
    private int offset;

    private List<BitSet> temp;
    private int tempFreq;

    private Map<BitSet, Integer> toUniqueIdx;
    private List<ClusterStats> clusters;
    private int idx;

    public ClusterTracker(boolean track){
        this.track = track;
        this.offset = 0;
        this.tempFreq = 0;
        this.idx = 0;

        if(track){
            this.temp = new ArrayList<BitSet>();
            this.toUniqueIdx = new HashMap<BitSet, Integer>();
            this.clusters = new ArrayList<ClusterStats>();
        }
    }

    public boolean shouldTrack(){
        return this.track;
    }

    public void setOffset(int offset){
        this.offset = offset;
    }

    public void setOffset(long offset){
        if(offset < Integer.MIN_VALUE || offset > Integer.MAX_VALUE){
            throw new ArithmeticException(
                    "Cluster ID offset exceeds the supported signed-integer range: " + offset
            );
        }

        this.offset = (int)offset;
    }

    public int getOffset(){
        return this.offset;
    }

    public void addAll(Set<BitSet> s, Map<BitSet, ReadFreq> reads){
        if(this.track){
            this.temp.addAll(s);

            for(BitSet umi : s){
                try{
                    this.tempFreq = Math.addExact(this.tempFreq, reads.get(umi).freq);
                }catch(ArithmeticException ex){
                    throw new ArithmeticException(
                            "Cluster frequency exceeds the supported maximum of "
                            + Integer.MAX_VALUE
                    );
                }
            }
        }
    }

    public void track(BitSet unique, Read read){
        if(this.track){
            for(BitSet s : this.temp)
                this.toUniqueIdx.put(s, idx);

            clusters.add(new ClusterStats(unique, tempFreq, read));

            this.temp.clear();
            this.tempFreq = 0;
            try{
                this.idx = Math.incrementExact(this.idx);
            }catch(ArithmeticException ex){
                throw new ArithmeticException(
                        "Cluster count exceeds the supported maximum of " + Integer.MAX_VALUE
                );
            }
        }
    }

    public int getId(BitSet umi){
        requireTracking();
        return toUniqueIdx.get(umi);
    }

    public ClusterStats getStats(int id){
        requireTracking();
        return clusters.get(id);
    }

    private void requireTracking(){
        if(!track)
            throw new IllegalStateException("Cluster tracking is disabled");
    }

    public static class ClusterStats{
        private BitSet umi;
        private int freq;
        private Read read;

        public ClusterStats(BitSet umi, int freq, Read read){
            this.umi = umi;
            this.freq = freq;
            this.read = read;
        }

        public BitSet getUMI(){
            return this.umi;
        }

        public int getFreq(){
            return this.freq;
        }

        public Read getRead(){
            return this.read;
        }
    }
}
