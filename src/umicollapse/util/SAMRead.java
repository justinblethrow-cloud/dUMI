package umicollapse.util;

import htsjdk.samtools.SAMRecord;

import java.util.Objects;
import java.util.regex.Pattern;
import java.util.regex.Matcher;

public class SAMRead extends Read{
    private static Pattern defaultUMIPattern;
    private static String defaultUMISeparator;
    private static boolean fastUnderscoreUMI;
    private SAMRecord record;
    private int avgQual;

    public SAMRead(SAMRecord record){
        this.record = record;
        this.avgQual = -1;
    }

    public static void setDefaultUMIPattern(String sep){
        defaultUMISeparator = Objects.requireNonNull(sep, "UMI separator must not be null");
        defaultUMIPattern = umiPattern(sep);
        fastUnderscoreUMI = sep.equals("_");
    }

    public static Pattern umiPattern(String sep){
        return Pattern.compile("^(.*)" + Pattern.quote(sep) + "([ATCGN]+)(.*?)$", Pattern.CASE_INSENSITIVE);
    }

    @Override
    public BitSet getUMI(int maxLength){
        if(fastUnderscoreUMI){
            String readName = record.getReadName();
            int start = findFastUMIStart(readName);
            int end = findFastUMIEnd(readName, start, maxLength);
            validateUMILength(readName, end - start, maxLength);

            return Utils.toBitSet(readName, start, end);
        }

        String readName = record.getReadName();
        String umi = extractUMI(readName);
        validateUMILength(readName, umi.length(), maxLength);
        if(maxLength >= 0 && umi.length() > maxLength)
            umi = umi.substring(0, maxLength);
        return Utils.toBitSet(umi.toUpperCase());
    }

    @Override
    public int getUMILength(){
        if(fastUnderscoreUMI){
            String readName = record.getReadName();
            int start = findFastUMIStart(readName);
            return findFastUMIEnd(readName, start, -1) - start;
        }

        return extractUMI(record.getReadName()).length();
    }

    @Override
    public int getAvgQual(){
        if(avgQual != -1)
            return avgQual;

        float avg = 0.0f;

        for(byte b : record.getBaseQualities())
            avg += b;

        avgQual = (int)(avg / record.getReadLength());
        return avgQual;
    }

    @Override
    public boolean equals(Object o){
        if(this == o)
            return true;

        if(!(o instanceof SAMRead))
            return false;

        SAMRead r = (SAMRead)o;
        return Objects.equals(record, r.record);
    }

    @Override
    public int hashCode(){
        return Objects.hashCode(record);
    }

    public int compareForTieBreak(SAMRead other){
        int cmp = compareNullableStrings(record.getReadName(), other.record.getReadName());

        if(cmp != 0)
            return cmp;

        return compareNullableStrings(record.getSAMString(), other.record.getSAMString());
    }

    public int getMapQual(){
        return record.getMappingQuality();
    }

    public SAMRecord toSAMRecord(){
        return record;
    }

    private static int findFastUMIStart(String readName){
        for(int i = readName.length() - 2; i >= 0; i--){
            if(readName.charAt(i) == '_' && Utils.isUMIBase(readName.charAt(i + 1)))
                return i + 1;
        }

        throw new IllegalArgumentException("Could not find UMI in read name: " + readName);
    }

    private static int findFastUMIEnd(String readName, int start, int maxLength){
        int end = start;

        while(end < readName.length()
                && (maxLength < 0 || end - start < maxLength)
                && Utils.isUMIBase(readName.charAt(end)))
            end++;

        return end;
    }

    private static String extractUMI(String readName){
        if(defaultUMIPattern == null)
            throw new IllegalStateException("UMI separator has not been configured");

        Matcher m = defaultUMIPattern.matcher(readName);

        if(!m.matches()){
            throw new IllegalArgumentException(
                "Could not find a UMI in read name '" + readName +
                "' using literal separator '" + defaultUMISeparator +
                "'; expected the separator followed by one or more A/T/C/G/N bases"
            );
        }

        return m.group(2);
    }

    private static void validateUMILength(String readName, int availableLength, int maxLength){
        if(maxLength >= 0 && availableLength < maxLength){
            throw new IllegalArgumentException(
                "UMI in read name '" + readName + "' is shorter than the requested length: " +
                "available " + availableLength + ", requested " + maxLength
            );
        }
    }

    private static int compareNullableStrings(String a, String b){
        if(a == b)
            return 0;
        if(a == null)
            return -1;
        if(b == null)
            return 1;
        return a.compareTo(b);
    }
}
