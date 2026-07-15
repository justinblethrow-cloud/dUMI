package umicollapse.util;

import htsjdk.samtools.SAMRecord;

import java.util.regex.Pattern;
import java.util.regex.Matcher;

public class SAMRead extends Read{
    private static Pattern defaultUMIPattern;
    private static boolean fastUnderscoreUMI;
    private SAMRecord record;
    private int avgQual;

    public SAMRead(SAMRecord record){
        this.record = record;
        this.avgQual = -1;
    }

    public static void setDefaultUMIPattern(String sep){
        defaultUMIPattern = umiPattern(sep);
        fastUnderscoreUMI = sep.equals("_");
    }

    public static Pattern umiPattern(String sep){
        return Pattern.compile("^(.*)" + sep + "([ATCGN]+)(.*?)$", Pattern.CASE_INSENSITIVE);
    }

    @Override
    public BitSet getUMI(int maxLength){
        if(fastUnderscoreUMI){
            String readName = record.getReadName();
            int start = findFastUMIStart(readName);
            int end = findFastUMIEnd(readName, start, maxLength);

            return Utils.toBitSet(readName, start, end);
        }

        Matcher m = defaultUMIPattern.matcher(record.getReadName());
        m.find();
        String umi = m.group(2);
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

        Matcher m = defaultUMIPattern.matcher(record.getReadName());
        m.find();
        return m.group(2).length();
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
        SAMRead r = (SAMRead)o;
        return record.equals(r.record);
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

        while(end < readName.length() && (maxLength < 0 || end - start < maxLength) && Utils.isUMIBase(readName.charAt(end)))
            end++;

        return end;
    }
}
