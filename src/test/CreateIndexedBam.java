package test;

import htsjdk.samtools.SAMFileWriter;
import htsjdk.samtools.SAMFileWriterFactory;
import htsjdk.samtools.SAMRecord;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.ValidationStringency;

import java.io.File;

public class CreateIndexedBam{
    public static void main(String[] args){
        if(args.length != 2)
            throw new IllegalArgumentException("usage: CreateIndexedBam <input.sam> <output.bam>");

        try(SamReader reader = SamReaderFactory.makeDefault()
                    .validationStringency(ValidationStringency.STRICT)
                    .open(new File(args[0]));
                SAMFileWriter writer = new SAMFileWriterFactory()
                    .setCreateIndex(true)
                    .makeBAMWriter(reader.getFileHeader(), true, new File(args[1]))){

            for(SAMRecord record : reader)
                writer.addAlignment(record);
        }catch(Exception ex){
            throw new RuntimeException(ex);
        }
    }
}
