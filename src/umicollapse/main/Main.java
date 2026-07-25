package umicollapse.main;

import java.io.File;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import umicollapse.algo.Adjacency;
import umicollapse.algo.Algo;
import umicollapse.algo.ConnectedComponents;
import umicollapse.algo.Directional;
import umicollapse.algo.ParallelAdjacency;
import umicollapse.algo.ParallelConnectedComponents;
import umicollapse.algo.ParallelDirectional;
import umicollapse.data.BKTree;
import umicollapse.data.Combo;
import umicollapse.data.Data;
import umicollapse.data.FenwickBKTree;
import umicollapse.data.Naive;
import umicollapse.data.Ngram;
import umicollapse.data.NgramBKTree;
import umicollapse.data.ParallelBKTree;
import umicollapse.data.ParallelFenwickBKTree;
import umicollapse.data.ParallelNaive;
import umicollapse.data.SortBKTree;
import umicollapse.data.SortNgramBKTree;
import umicollapse.data.SymmetricDelete;
import umicollapse.data.Trie;
import umicollapse.merge.AnyMerge;
import umicollapse.merge.AvgQualMerge;
import umicollapse.merge.MapQualMerge;
import umicollapse.merge.Merge;

public class Main{
    private static final Set<String> VALUE_OPTIONS = Set.of(
            "-i", "-o", "-k", "-u", "-p", "-t", "-T",
            "--algo", "--data", "--merge", "--umi-sep", "--streaming-mode"
    );
    private static final Set<String> FLAG_OPTIONS = Set.of(
            "--two-pass", "--paired", "--remove-unpaired", "--remove-chimeric",
            "--keep-unmapped", "--tag"
    );

    private static final Map<String, Class<? extends Algo>> SEQUENTIAL_ALGOS;
    private static final Map<String, Class<? extends Algo>> PARALLEL_ALGOS;
    private static final Map<String, Class<? extends Data>> SEQUENTIAL_DATA;
    private static final Map<String, Class<? extends Data>> PARALLEL_DATA;
    private static final Map<String, Class<? extends Merge>> MERGES;

    static{
        Map<String, Class<? extends Algo>> sequentialAlgos = new LinkedHashMap<>();
        sequentialAlgos.put("adj", Adjacency.class);
        sequentialAlgos.put("dir", Directional.class);
        sequentialAlgos.put("cc", ConnectedComponents.class);
        SEQUENTIAL_ALGOS = Collections.unmodifiableMap(sequentialAlgos);

        Map<String, Class<? extends Algo>> parallelAlgos = new LinkedHashMap<>();
        parallelAlgos.put("adj", ParallelAdjacency.class);
        parallelAlgos.put("dir", ParallelDirectional.class);
        parallelAlgos.put("cc", ParallelConnectedComponents.class);
        PARALLEL_ALGOS = Collections.unmodifiableMap(parallelAlgos);

        Map<String, Class<? extends Data>> sequentialData = new LinkedHashMap<>();
        sequentialData.put("naive", Naive.class);
        sequentialData.put("combo", Combo.class);
        sequentialData.put("ngram", Ngram.class);
        sequentialData.put("delete", SymmetricDelete.class);
        sequentialData.put("trie", Trie.class);
        sequentialData.put("bktree", BKTree.class);
        sequentialData.put("sortbktree", SortBKTree.class);
        sequentialData.put("ngrambktree", NgramBKTree.class);
        sequentialData.put("sortngrambktree", SortNgramBKTree.class);
        sequentialData.put("fenwickbktree", FenwickBKTree.class);
        SEQUENTIAL_DATA = Collections.unmodifiableMap(sequentialData);

        Map<String, Class<? extends Data>> parallelData = new LinkedHashMap<>();
        parallelData.put("naive", ParallelNaive.class);
        parallelData.put("bktree", ParallelBKTree.class);
        parallelData.put("fenwickbktree", ParallelFenwickBKTree.class);
        PARALLEL_DATA = Collections.unmodifiableMap(parallelData);

        Map<String, Class<? extends Merge>> merges = new LinkedHashMap<>();
        merges.put("any", AnyMerge.class);
        merges.put("avgqual", AvgQualMerge.class);
        merges.put("mapqual", MapQualMerge.class);
        MERGES = Collections.unmodifiableMap(merges);
    }

    public static void main(String[] args){
        try{
            run(args);
        }catch(CliException ex){
            System.err.println("error: " + ex.getMessage());
            System.err.println("Run 'umicollapse --help' for usage.");
            System.exit(2);
        }
    }

    private static void run(String[] args){
        if(args.length == 0)
            throw new CliException("No arguments specified.");

        if(isHelp(args)){
            printHelp();
            return;
        }

        if(args.length == 1 && args[0].equals("--version")){
            printVersion();
            return;
        }

        long startTime = System.currentTimeMillis();
        Config config = parse(args);
        validate(config);

        Algo algorithm = instantiate(config.algorithmClass, "algorithm", config.algorithm);
        Merge merge = instantiate(config.mergeClass, "merge method", config.merge);

        Path temporaryOutput = createTemporaryOutput(config.output, config.mode);
        boolean promoted = false;

        System.out.println("Arguments\t" + Arrays.toString(args));

        try{
            File stagedOutput = temporaryOutput.toFile();

            if(config.mode.equals("fastq")){
                DeduplicateFASTQ dedup = new DeduplicateFASTQ();
                dedup.deduplicateAndMergeCore(
                        config.input.toFile(), stagedOutput, algorithm, config.dataClass,
                        merge, config.umiLength, config.k, config.percentage,
                        config.parallelAlign, config.trackClusters
                );
            }else{
                DeduplicateSAM dedup = new DeduplicateSAM();

                if(config.twoPass){
                    dedup.deduplicateAndMergeTwoPassCore(
                            config.input.toFile(), stagedOutput, algorithm, config.dataClass,
                            merge, config.umiLength, config.k, config.percentage,
                            config.umiSeparator, config.paired, config.removeUnpaired,
                            config.removeChimeric, config.keepUnmapped, config.trackClusters
                    );
                }else{
                    dedup.deduplicateAndMergeCore(
                            config.input.toFile(), stagedOutput, algorithm, config.dataClass,
                            merge, config.umiLength, config.k, config.percentage,
                            config.parallelAlign, config.umiSeparator, config.paired,
                            config.removeUnpaired, config.removeChimeric, config.keepUnmapped,
                            config.trackClusters, config.streamingMode
                    );
                }
            }

            promoteOutput(temporaryOutput, config.output);
            promoted = true;
        }finally{
            if(!promoted)
                deleteTemporaryOutput(temporaryOutput);
        }

        System.out.println("UMI collapsing finished in "
                + ((System.currentTimeMillis() - startTime) / 1000.0) + " seconds!");
    }

    private static Config parse(String[] args){
        Config config = new Config();
        config.mode = args[0].toLowerCase(Locale.ROOT);

        if(!config.mode.equals("fastq") && !config.mode.equals("sam") && !config.mode.equals("bam"))
            throw new CliException("Unknown mode '" + args[0] + "'; expected fastq, sam, or bam.");

        Map<String, String> values = new HashMap<>();
        Set<String> flags = new java.util.HashSet<>();

        for(int i = 1; i < args.length; i++){
            String token = args[i];
            String option = token;
            String inlineValue = null;
            int equals = token.indexOf('=');

            if(equals > 2 && token.startsWith("--")){
                option = token.substring(0, equals);
                inlineValue = token.substring(equals + 1);
            }

            if(VALUE_OPTIONS.contains(option)){
                if(values.containsKey(option))
                    throw new CliException("Option " + option + " may only be specified once.");

                String value;

                if(inlineValue != null){
                    if(inlineValue.isEmpty())
                        throw new CliException("Option " + option + " requires a value.");
                    value = inlineValue;
                }else{
                    if(i + 1 >= args.length || isKnownOptionToken(args[i + 1]))
                        throw new CliException("Option " + option + " requires a value.");
                    value = args[++i];
                }

                values.put(option, value);
            }else if(FLAG_OPTIONS.contains(option)){
                if(inlineValue != null)
                    throw new CliException("Flag " + option + " does not accept a value.");
                if(!flags.add(option))
                    throw new CliException("Option " + option + " may only be specified once.");
            }else if(token.equals("--help") || token.equals("-h") || token.equals("--version")){
                throw new CliException(token + " must be used without a mode or other options.");
            }else if(token.startsWith("-")){
                throw new CliException("Unknown option '" + token + "'.");
            }else{
                throw new CliException("Unexpected positional argument '" + token + "'.");
            }
        }

        config.input = requiredPath(values, "-i", "input");
        config.output = requiredPath(values, "-o", "output");
        config.k = parseInteger(values.getOrDefault("-k", "1"), "-k");
        config.umiLength = parseInteger(values.getOrDefault("-u", "-1"), "-u");
        config.percentage = parseFloat(values.getOrDefault("-p", "0.5"), "-p");
        config.algorithm = values.getOrDefault("--algo", "dir").toLowerCase(Locale.ROOT);
        config.merge = values.getOrDefault(
                "--merge", config.mode.equals("fastq") ? "avgqual" : "mapqual"
        ).toLowerCase(Locale.ROOT);
        config.umiSeparatorSpecified = values.containsKey("--umi-sep");
        config.umiSeparator = values.getOrDefault("--umi-sep", "_");
        config.dataSpecified = values.containsKey("--data");
        config.data = values.getOrDefault("--data", "ngrambktree").toLowerCase(Locale.ROOT);
        config.streamingModeSpecified = values.containsKey("--streaming-mode");
        config.streamingMode = values.getOrDefault(
                "--streaming-mode",
                System.getProperty("umicollapse.streaming.mode", "auto")
        ).toLowerCase(Locale.ROOT);

        config.twoPass = flags.contains("--two-pass");
        config.paired = flags.contains("--paired");
        config.removeUnpaired = flags.contains("--remove-unpaired");
        config.removeChimeric = flags.contains("--remove-chimeric");
        config.keepUnmapped = flags.contains("--keep-unmapped");
        config.trackClusters = flags.contains("--tag");

        boolean hasParallelAlign = values.containsKey("-t");
        boolean hasParallelData = values.containsKey("-T");

        if(hasParallelAlign && hasParallelData)
            throw new CliException("-t and -T are mutually exclusive.");

        if(hasParallelAlign){
            config.threadCount = parseInteger(values.get("-t"), "-t");
            config.parallelAlign = true;
        }else if(hasParallelData){
            config.threadCount = parseInteger(values.get("-T"), "-T");
            config.parallelData = true;
            if(!config.dataSpecified)
                config.data = "bktree";
        }

        return config;
    }

    private static void validate(Config config){
        if(config.k < 0)
            throw new CliException("-k must be zero or greater.");

        if(config.umiLength != -1 && config.umiLength <= 0)
            throw new CliException("-u must be -1 (autodetect) or a positive integer.");

        if(!config.mode.equals("fastq") && config.umiLength > 0 && config.k >= config.umiLength)
            throw new CliException("-k must be smaller than the explicitly configured UMI length (-u).");

        if(!Float.isFinite(config.percentage) || config.percentage < 0)
            throw new CliException("-p must be a finite, non-negative number.");

        if((config.parallelAlign || config.parallelData) && config.threadCount <= 0)
            throw new CliException((config.parallelAlign ? "-t" : "-T") + " must be a positive integer.");

        if(!SEQUENTIAL_ALGOS.containsKey(config.algorithm))
            throw new CliException("Invalid --algo '" + config.algorithm + "'; expected one of "
                    + String.join(", ", SEQUENTIAL_ALGOS.keySet()) + ".");

        if(!MERGES.containsKey(config.merge))
            throw new CliException("Invalid --merge '" + config.merge + "'; expected one of "
                    + String.join(", ", MERGES.keySet()) + ".");

        Map<String, Class<? extends Algo>> algorithms =
                config.parallelData ? PARALLEL_ALGOS : SEQUENTIAL_ALGOS;
        Map<String, Class<? extends Data>> dataStructures =
                config.parallelData ? PARALLEL_DATA : SEQUENTIAL_DATA;

        if(!dataStructures.containsKey(config.data)){
            String prefix = config.parallelData ? "Invalid --data for -T" : "Invalid --data";
            throw new CliException(prefix + " '" + config.data + "'; expected one of "
                    + String.join(", ", dataStructures.keySet()) + ".");
        }

        config.algorithmClass = algorithms.get(config.algorithm);
        config.dataClass = dataStructures.get(config.data);
        config.mergeClass = MERGES.get(config.merge);

        if(config.mode.equals("fastq")){
            if(config.streamingModeSpecified)
                throw new CliException("--streaming-mode is only supported in sam or bam mode.");

            if(config.twoPass || config.paired || config.removeUnpaired || config.removeChimeric
                    || config.keepUnmapped || config.umiSeparatorSpecified){
                throw new CliException(
                        "--two-pass, --paired, --remove-unpaired, --remove-chimeric, "
                        + "--keep-unmapped, --umi-sep, and --streaming-mode are only supported "
                        + "in sam or bam mode."
                );
            }

            if(config.merge.equals("mapqual"))
                throw new CliException("--merge mapqual is not supported in fastq mode.");
        }else if(config.umiSeparator.isEmpty()){
            throw new CliException("--umi-sep requires a non-empty literal separator.");
        }

        if(!config.streamingMode.equals("auto")
                && !config.streamingMode.equals("on")
                && !config.streamingMode.equals("off")){
            throw new CliException("Invalid --streaming-mode '" + config.streamingMode
                    + "'; expected auto, on, or off.");
        }

        if(config.streamingMode.equals("on") && config.twoPass)
            throw new CliException("--streaming-mode on cannot be combined with --two-pass.");

        if(config.streamingMode.equals("on")
                && (config.paired || config.parallelAlign
                    || config.parallelData || config.trackClusters)){
            throw new CliException(
                    "Streaming mode requires single-end, single-threaded execution "
                    + "without --tag."
            );
        }

        if(config.trackClusters && config.twoPass)
            throw new CliException("Cannot combine --tag with --two-pass.");

        if(config.parallelAlign && config.twoPass)
            throw new CliException("Cannot combine -t with --two-pass; two-pass mode is sequential.");

        if(config.parallelAlign && config.trackClusters)
            throw new CliException("Cannot combine -t with --tag because cluster IDs would be nondeterministic.");

        if(config.parallelData && config.trackClusters)
            throw new CliException("Cannot combine -T with --tag.");

        if(config.paired && config.parallelAlign)
            throw new CliException("Cannot combine --paired with -t.");

        if(config.paired && config.keepUnmapped)
            throw new CliException("Cannot combine --paired with --keep-unmapped.");

        if((config.removeUnpaired || config.removeChimeric) && !config.paired)
            throw new CliException("--remove-unpaired and --remove-chimeric require --paired.");

        validatePaths(config);

        if(config.parallelAlign || config.parallelData){
            System.setProperty(
                    "java.util.concurrent.ForkJoinPool.common.parallelism",
                    Integer.toString(config.threadCount - 1)
            );
        }
    }

    private static void validatePaths(Config config){
        Path input;

        try{
            input = config.input.toRealPath();
        }catch(IOException ex){
            throw new CliException("Input file does not exist or cannot be resolved: " + config.input + ".", ex);
        }

        if(!Files.isRegularFile(input) || !Files.isReadable(input))
            throw new CliException("Input is not a readable regular file: " + input + ".");

        Path output = config.output.toAbsolutePath().normalize();
        Path parent = output.getParent();

        if(parent == null)
            throw new CliException("Output has no parent directory: " + output + ".");

        Path realParent;

        try{
            realParent = parent.toRealPath();
        }catch(IOException ex){
            throw new CliException("Output directory does not exist or cannot be resolved: " + parent + ".", ex);
        }

        if(!Files.isDirectory(realParent))
            throw new CliException("Output parent is not a directory: " + realParent + ".");

        if(Files.exists(output)){
            if(Files.isDirectory(output))
                throw new CliException("Output path is a directory: " + output + ".");

            try{
                if(Files.isSameFile(input, output))
                    throw new CliException("Input and output must be different files.");
            }catch(IOException ex){
                throw new CliException("Could not compare input and output paths.", ex);
            }
        }else{
            Path resolvedOutput = realParent.resolve(output.getFileName()).normalize();

            if(input.equals(resolvedOutput))
                throw new CliException("Input and output must be different files.");
        }

        config.input = input;
        config.output = output;
    }

    private static Path requiredPath(Map<String, String> values, String option, String description){
        String value = values.get(option);

        if(value == null)
            throw new CliException("Missing required " + description + " option " + option + ".");

        if(value.isEmpty())
            throw new CliException("Option " + option + " requires a non-empty path.");

        return Path.of(value);
    }

    private static int parseInteger(String value, String option){
        try{
            return Integer.parseInt(value);
        }catch(NumberFormatException ex){
            throw new CliException("Option " + option + " requires an integer, not '" + value + "'.", ex);
        }
    }

    private static float parseFloat(String value, String option){
        try{
            return Float.parseFloat(value);
        }catch(NumberFormatException ex){
            throw new CliException("Option " + option + " requires a number, not '" + value + "'.", ex);
        }
    }

    private static <T> T instantiate(Class<? extends T> type, String description, String name){
        try{
            return type.getDeclaredConstructor().newInstance();
        }catch(ReflectiveOperationException ex){
            throw new IllegalStateException("Could not initialize " + description + " '" + name + "'.", ex);
        }
    }

    private static Path createTemporaryOutput(Path output, String mode){
        String suffix = temporarySuffix(output, mode);
        Path parent = output.getParent();

        for(int attempt = 0; attempt < 100; attempt++){
            Path candidate = parent.resolve(".dumi-output-" + UUID.randomUUID() + suffix);

            try{
                Files.newOutputStream(
                        candidate, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE
                ).close();
                return candidate;
            }catch(FileAlreadyExistsException ex){
                // An astronomically unlikely name collision; choose a new name.
            }catch(IOException ex){
                throw new CliException("Could not create a temporary output next to " + output + ".", ex);
            }
        }

        throw new CliException("Could not allocate a unique temporary output next to " + output + ".");
    }

    private static String temporarySuffix(Path output, String mode){
        String name = output.getFileName().toString().toLowerCase(Locale.ROOT);

        if(!mode.equals("fastq"))
            return name.endsWith(".sam") ? ".sam" : ".bam";

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

    private static void promoteOutput(Path temporaryOutput, Path output){
        try{
            try{
                Files.move(
                        temporaryOutput, output,
                        StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING
                );
            }catch(AtomicMoveNotSupportedException ex){
                Files.move(temporaryOutput, output, StandardCopyOption.REPLACE_EXISTING);
            }
        }catch(IOException ex){
            throw new IllegalStateException("Could not promote completed output to " + output + ".", ex);
        }
    }

    private static void deleteTemporaryOutput(Path temporaryOutput){
        try{
            Files.deleteIfExists(temporaryOutput);
        }catch(IOException ex){
            System.err.println("warning: could not remove incomplete temporary output "
                    + temporaryOutput + ": " + ex.getMessage());
        }
    }

    private static boolean isKnownOptionToken(String token){
        String option = token;
        int equals = token.indexOf('=');

        if(equals > 2 && token.startsWith("--"))
            option = token.substring(0, equals);

        return VALUE_OPTIONS.contains(option) || FLAG_OPTIONS.contains(option)
                || option.equals("--help") || option.equals("-h") || option.equals("--version");
    }

    private static boolean isHelp(String[] args){
        return args.length == 1 && (args[0].equals("--help") || args[0].equals("-h"));
    }

    private static void printVersion(){
        Package mainPackage = Main.class.getPackage();
        String version = mainPackage == null ? null : mainPackage.getImplementationVersion();
        System.out.println("dUMI " + (version == null ? "development" : version));
    }

    private static void printHelp(){
        System.out.println(
                "Usage: umicollapse <fastq|sam|bam> -i INPUT -o OUTPUT [options]\n"
                + "       umicollapse --help\n"
                + "       umicollapse --version\n"
                + "\n"
                + "Core options:\n"
                + "  -k N                 substitution edits allowed (default: 1)\n"
                + "  -u N                 UMI length; -1 autodetects (default: -1)\n"
                + "  -p FRACTION          directional threshold (default: 0.5)\n"
                + "  --algo NAME          adj, dir, or cc (default: dir)\n"
                + "  --data NAME          data structure (default: ngrambktree; with -T: bktree)\n"
                + "  --merge NAME         any, avgqual, or mapqual\n"
                + "  -t N                  parallelize alignment groups\n"
                + "  -T N                  parallelize within an alignment group\n"
                + "  --tag                 mark cluster membership instead of removing duplicates\n"
                + "\n"
                + "SAM/BAM options:\n"
                + "  --umi-sep STRING      literal UMI separator (default: _)\n"
                + "  --two-pass            use the lower-memory two-pass path\n"
                + "  --paired              process paired-end alignments\n"
                + "  --remove-unpaired     discard unpaired reads (requires --paired)\n"
                + "  --remove-chimeric      discard cross-reference pairs (requires --paired)\n"
                + "  --keep-unmapped       retain unmapped single-end reads\n"
                + "  --streaming-mode MODE auto, on, or off (default: auto)\n"
        );
    }

    private static final class Config{
        private String mode;
        private Path input;
        private Path output;
        private String algorithm;
        private String data;
        private String merge;
        private String umiSeparator;
        private String streamingMode;
        private int k;
        private int umiLength;
        private int threadCount;
        private float percentage;
        private boolean dataSpecified;
        private boolean umiSeparatorSpecified;
        private boolean streamingModeSpecified;
        private boolean parallelData;
        private boolean parallelAlign;
        private boolean twoPass;
        private boolean paired;
        private boolean removeUnpaired;
        private boolean removeChimeric;
        private boolean keepUnmapped;
        private boolean trackClusters;
        private Class<? extends Algo> algorithmClass;
        private Class<? extends Data> dataClass;
        private Class<? extends Merge> mergeClass;
    }

    private static final class CliException extends IllegalArgumentException{
        private static final long serialVersionUID = 1L;

        private CliException(String message){
            super(message);
        }

        private CliException(String message, Throwable cause){
            super(message, cause);
        }
    }
}
