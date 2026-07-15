package test;

import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.ValidationStringency;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class InspectAlignmentFile{
    public static void main(String[] args){
        if(args.length != 2)
            throw new IllegalArgumentException("usage: InspectAlignmentFile <records|names|sort-order|count> <sam-or-bam>");

        SamReader reader = SamReaderFactory.makeDefault()
                .validationStringency(ValidationStringency.STRICT)
                .open(new File(args[1]));

        try{
            if(args[0].equals("sort-order")){
                System.out.println(reader.getFileHeader().getSortOrder());
                return;
            }

            List<String> values = new ArrayList<>();
            for(SAMRecord record : reader){
                if(args[0].equals("records"))
                    values.add(record.getSAMString().trim());
                else if(args[0].equals("names"))
                    values.add(record.getReadName());
                else if(args[0].equals("count"))
                    values.add("record");
                else
                    throw new IllegalArgumentException("unknown inspection mode: " + args[0]);
            }

            if(args[0].equals("count")){
                System.out.println(values.size());
            }else{
                Collections.sort(values);
                for(String value : values)
                    System.out.println(value);
            }
        }finally{
            try{
                reader.close();
            }catch(Exception ex){
                throw new RuntimeException(ex);
            }
        }
    }
}
