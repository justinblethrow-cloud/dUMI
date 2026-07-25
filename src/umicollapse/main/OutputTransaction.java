package umicollapse.main;

import java.io.File;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Locale;
import java.util.Objects;

final class OutputTransaction{
    @FunctionalInterface
    interface Operation{
        void run(File stagedOutput);
    }

    private OutputTransaction(){
    }

    static void runAlignment(File input, File output, Operation operation){
        run(input, output, alignmentSuffix(output), operation);
    }

    static void runFastq(File input, File output, Operation operation){
        run(input, output, fastqSuffix(output), operation);
    }

    private static void run(File input, File output, String suffix, Operation operation){
        Objects.requireNonNull(input, "input");
        Objects.requireNonNull(output, "output");
        Objects.requireNonNull(operation, "operation");

        Path inputPath = input.toPath().toAbsolutePath().normalize();
        Path outputPath = output.toPath().toAbsolutePath().normalize();
        validateDistinctFiles(inputPath, outputPath);

        Path parent = outputPath.getParent();

        if(parent == null || !Files.isDirectory(parent))
            throw new IllegalArgumentException("Output directory does not exist: " + parent);
        if(Files.isDirectory(outputPath))
            throw new IllegalArgumentException("Output path is a directory: " + outputPath);

        Path stagedOutput;

        try{
            stagedOutput = Files.createTempFile(parent, ".dumi-output-", suffix);
        }catch(IOException ex){
            throw new IllegalStateException(
                    "Could not create a temporary output next to " + outputPath,
                    ex
            );
        }

        boolean promoted = false;

        try{
            operation.run(stagedOutput.toFile());
            promote(stagedOutput, outputPath);
            promoted = true;
        }catch(RuntimeException | Error ex){
            try{
                Files.deleteIfExists(stagedOutput);
            }catch(IOException deleteEx){
                ex.addSuppressed(deleteEx);
            }

            throw ex;
        }finally{
            if(!promoted){
                try{
                    Files.deleteIfExists(stagedOutput);
                }catch(IOException ex){
                    System.err.println(
                            "warning: could not remove incomplete temporary output "
                            + stagedOutput + ": " + ex.getMessage()
                    );
                }
            }
        }
    }

    private static void validateDistinctFiles(Path input, Path output){
        if(input.equals(output))
            throw new IllegalArgumentException("Input and output must be different files");

        if(!Files.exists(input) || !Files.exists(output))
            return;

        try{
            if(Files.isSameFile(input, output))
                throw new IllegalArgumentException("Input and output must be different files");
        }catch(IOException ex){
            throw new IllegalStateException("Could not compare input and output paths", ex);
        }
    }

    private static String alignmentSuffix(File output){
        String name = output.getName().toLowerCase(Locale.ROOT);
        return name.endsWith(".sam") ? ".sam" : ".bam";
    }

    private static String fastqSuffix(File output){
        String name = output.getName().toLowerCase(Locale.ROOT);

        if(name.endsWith(".fastq.gz"))
            return ".fastq.gz";
        if(name.endsWith(".fq.gz"))
            return ".fq.gz";
        if(name.endsWith(".fastq"))
            return ".fastq";
        if(name.endsWith(".fq"))
            return ".fq";
        if(name.endsWith(".gz"))
            return ".fastq.gz";

        return ".fastq";
    }

    private static void promote(Path stagedOutput, Path output){
        try{
            try{
                Files.move(
                        stagedOutput,
                        output,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING
                );
            }catch(AtomicMoveNotSupportedException ex){
                Files.move(stagedOutput, output, StandardCopyOption.REPLACE_EXISTING);
            }
        }catch(IOException ex){
            throw new IllegalStateException("Could not promote completed output to " + output, ex);
        }
    }
}
