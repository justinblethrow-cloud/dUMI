package umicollapse.util;

public class ReadFreq{
    public Read read;
    public int freq;

    public ReadFreq(Read read, int freq){
        this.read = read;
        this.freq = freq;
    }

    /**
     * Increment the frequency without allowing the signed integer counter to
     * wrap into a negative value. The surrounding algorithm interfaces use
     * integer frequencies, so an exact failure is safer than silently changing
     * the ordering and clustering semantics.
     */
    public void increment(){
        try{
            this.freq = Math.incrementExact(this.freq);
        }catch(ArithmeticException ex){
            throw new ArithmeticException(
                    "Read frequency exceeds the supported maximum of " + Integer.MAX_VALUE
            );
        }
    }
}
