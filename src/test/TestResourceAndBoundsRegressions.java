package test;

import com.sun.management.UnixOperatingSystemMXBean;
import htsjdk.samtools.SAMFileHeader;
import htsjdk.samtools.SAMRecordIterator;
import htsjdk.samtools.SamReader;
import htsjdk.samtools.SamReaderFactory;
import htsjdk.samtools.ValidationStringency;

import java.io.File;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

import umicollapse.algo.Directional;
import umicollapse.data.DataStructure;
import umicollapse.data.NgramBKTree;
import umicollapse.main.DeduplicateFASTQ;
import umicollapse.main.DeduplicateSAM;
import umicollapse.merge.AnyMerge;
import umicollapse.util.BitSet;
import umicollapse.util.ClusterTracker;
import umicollapse.util.ReadFreq;
import umicollapse.util.Utils;

public class TestResourceAndBoundsRegressions{
    private static final int DESCRIPTOR_CLEANUP_REPETITIONS = 16;
    private static final long DESCRIPTOR_NOISE_TOLERANCE = 4L;

    public static void main(String[] args) throws Exception{
        Path temp = Files.createTempDirectory("dumi-resource-bounds-test.");

        try{
            testAutodetectedKParity(temp);
            testReverseStrandCoordinateOverflow(temp);
            testDirectApiTransactionalSafety(temp);
            testMalformedSamCleanup(temp);
            testMalformedFastqCleanup(temp);
            testRepeatedMalformedInputDescriptorCleanup(temp);
            testDataConstructionFailureIsFatal(temp);
            testExactFrequencyFailures();
        }finally{
            deleteRecursively(temp);
        }

        System.out.println("Passed: resource and bounds regressions");
    }

    private static void testAutodetectedKParity(Path temp) throws Exception{
        File input = writeSingleSam(temp.resolve("autodetect.sam"));

        expectIllegalArgument(
                () -> deduplicateSam(
                        input,
                        temp.resolve("invalid-zero-length.sam").toFile(),
                        0,
                        0,
                        "off"
                ),
                "UMI length",
                "positive"
        );
        expectIllegalArgument(
                () -> deduplicateSam(
                        input,
                        temp.resolve("invalid-negative-k.sam").toFile(),
                        -1,
                        -1,
                        "off"
                ),
                "k must be non-negative"
        );
        expectIllegalArgument(
                () -> deduplicateSam(input, temp.resolve("invalid-off.sam").toFile(), 4, "off"),
                "k=4",
                "effective UMI length=4"
        );
        expectIllegalArgument(
                () -> deduplicateSam(input, temp.resolve("invalid-on.sam").toFile(), 4, "on"),
                "k=4",
                "effective UMI length=4"
        );
        expectIllegalArgument(
                () -> deduplicateSamTwoPass(input, temp.resolve("invalid-two-pass.sam").toFile(), 4),
                "k=4",
                "effective UMI length=4"
        );

        File off = temp.resolve("valid-off.sam").toFile();
        File on = temp.resolve("valid-on.sam").toFile();
        File twoPass = temp.resolve("valid-two-pass.sam").toFile();
        deduplicateSam(input, off, 3, "off");
        deduplicateSam(input, on, 3, "on");
        deduplicateSamTwoPass(input, twoPass, 3);

        assertRecordCount(off, 1, "streaming off");
        assertRecordCount(on, 1, "streaming on");
        assertRecordCount(twoPass, 1, "two-pass");
    }

    private static void testReverseStrandCoordinateOverflow(Path temp) throws Exception{
        Path input = temp.resolve("reverse-coordinate-overflow.sam");
        Files.write(
                input,
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:2147483647",
                        "edge1_AAAA\t16\tchr1\t2147483640\t60\t8M1H\t*\t0\t0\tAAAAAAAA\tIIIIIIII",
                        "edge2_AAAA\t16\tchr1\t2147483640\t60\t8M1H\t*\t0\t0\tAAAAAAAA\tIIIIIIII"
                ),
                StandardCharsets.US_ASCII
        );

        File legacyOutput = temp.resolve("reverse-overflow-off.sam").toFile();
        File autoOutput = temp.resolve("reverse-overflow-auto.sam").toFile();
        deduplicateSam(input.toFile(), legacyOutput, 1, "off");
        deduplicateSam(input.toFile(), autoOutput, 1, "auto");
        assertRecordCount(legacyOutput, 1, "reverse-overflow legacy path");
        assertRecordCount(autoOutput, 1, "reverse-overflow automatic fallback");
        assertSortOrder(autoOutput, SAMFileHeader.SortOrder.coordinate, "reverse-overflow fallback");

        Path forcedOutput = temp.resolve("reverse-overflow-on.sam");
        byte[] marker = "preserve-reverse-overflow-output\n".getBytes(StandardCharsets.US_ASCII);
        Files.write(forcedOutput, marker);
        expectRuntimeMessage(
                () -> deduplicateSam(input.toFile(), forcedOutput.toFile(), 1, "on"),
                "reverse-strand unclipped end",
                "exceeds"
        );
        assertBytesEqual(
                marker,
                Files.readAllBytes(forcedOutput),
                "forced reverse-overflow streaming failure replaced its destination"
        );
        assertNoTransactionTemps(temp);
    }

    private static void testDirectApiTransactionalSafety(Path temp) throws Exception{
        File samInput = writeSingleSam(temp.resolve("direct-valid.sam"));
        File fastqInput = writeValidFastq(temp.resolve("direct-valid.fastq"));

        assertSameAndHardlinkRejected(
                temp, "legacy-sam", samInput,
                (input, output) -> deduplicateSam(input, output, 1, "off")
        );
        assertSameAndHardlinkRejected(
                temp, "streaming-sam", samInput,
                (input, output) -> deduplicateSam(input, output, 1, "on")
        );
        assertSameAndHardlinkRejected(
                temp, "two-pass-sam", samInput,
                (input, output) -> deduplicateSamTwoPass(input, output, 1)
        );
        assertSameAndHardlinkRejected(
                temp, "fastq", fastqInput,
                TestResourceAndBoundsRegressions::deduplicateFastq
        );

        Path malformedSam = temp.resolve("direct-transaction-malformed.sam");
        Files.write(
                malformedSam,
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:1000",
                        "valid_AAAA\t0\tchr1\t1\t60\t4M\t*\t0\t0\tAAAA\tIIII",
                        "broken_CCCC\t0\tchr1"
                ),
                StandardCharsets.US_ASCII
        );
        Path malformedFastq = temp.resolve("direct-transaction-malformed.fastq");
        Files.write(
                malformedFastq,
                Arrays.asList("@valid", "AAAA", "+", "IIII", "@broken", "CCCC", "+"),
                StandardCharsets.US_ASCII
        );

        assertFailurePreservesDestination(
                temp, "legacy-sam", malformedSam.toFile(), ".sam",
                (input, output) -> deduplicateSam(input, output, 1, "off")
        );
        assertFailurePreservesDestination(
                temp, "streaming-sam", malformedSam.toFile(), ".sam",
                (input, output) -> deduplicateSam(input, output, 1, "on")
        );
        assertFailurePreservesDestination(
                temp, "two-pass-sam", malformedSam.toFile(), ".sam",
                (input, output) -> deduplicateSamTwoPass(input, output, 1)
        );
        assertFailurePreservesDestination(
                temp, "fastq", malformedFastq.toFile(), ".fastq",
                TestResourceAndBoundsRegressions::deduplicateFastq
        );
        assertNoTransactionTemps(temp);
    }

    private static void assertSameAndHardlinkRejected(
            Path temp,
            String description,
            File input,
            DirectDeduplicator deduplicator) throws Exception{
        byte[] expected = Files.readAllBytes(input.toPath());
        expectIllegalArgument(
                () -> deduplicator.run(input, input),
                "Input and output must be different files"
        );
        assertBytesEqual(
                expected,
                Files.readAllBytes(input.toPath()),
                description + " same-path rejection modified the input"
        );

        Path hardlink = temp.resolve(description + "-hardlink" + extension(input));
        Files.createLink(hardlink, input.toPath());
        expectIllegalArgument(
                () -> deduplicator.run(input, hardlink.toFile()),
                "Input and output must be different files"
        );
        assertBytesEqual(
                expected,
                Files.readAllBytes(input.toPath()),
                description + " hardlink rejection modified the input"
        );
        Files.delete(hardlink);
    }

    private static void assertFailurePreservesDestination(
            Path temp,
            String description,
            File malformedInput,
            String suffix,
            DirectDeduplicator deduplicator) throws Exception{
        Path output = temp.resolve(description + "-preexisting" + suffix);
        byte[] marker = ("preserve-" + description + "\n").getBytes(StandardCharsets.US_ASCII);
        Files.write(output, marker);
        expectRuntime(() -> deduplicator.run(malformedInput, output.toFile()));
        assertBytesEqual(
                marker,
                Files.readAllBytes(output),
                description + " failure replaced its preexisting destination"
        );
    }

    private static String extension(File file){
        String name = file.getName();
        int dot = name.lastIndexOf('.');
        return dot < 0 ? ".out" : name.substring(dot);
    }

    private static void assertNoTransactionTemps(Path directory) throws Exception{
        try(Stream<Path> children = Files.list(directory)){
            if(children.anyMatch(path -> {
                String name = path.getFileName().toString();
                return name.startsWith(".dumi-output-") || name.startsWith(".dumi-stream-");
            })){
                throw new AssertionError("direct API call left a temporary output behind");
            }
        }
    }

    private static void assertBytesEqual(byte[] expected, byte[] actual, String message){
        if(!Arrays.equals(expected, actual))
            throw new AssertionError(message);
    }

    private static void testMalformedSamCleanup(Path temp) throws Exception{
        Path input = temp.resolve("malformed.sam");
        Files.write(
                input,
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:1000",
                        "valid_AAAA\t0\tchr1\t1\t60\t4M\t*\t0\t0\tAAAA\tIIII",
                        "broken_CCCC\t0\tchr1"
                ),
                StandardCharsets.US_ASCII
        );

        File output = temp.resolve("malformed-output.sam").toFile();
        expectRuntime(() -> deduplicateSam(input.toFile(), output, 1, "on"));

        if(output.exists())
            throw new AssertionError("streaming failure promoted a partial SAM output");

        try(Stream<Path> children = Files.list(temp)){
            if(children.anyMatch(path -> path.getFileName().toString().startsWith(".dumi-stream-")))
                throw new AssertionError("streaming failure left a temporary output behind");
        }

        File legacyOutput = temp.resolve("malformed-legacy-output.sam").toFile();
        expectRuntime(() -> deduplicateSam(input.toFile(), legacyOutput, 1, "off"));
        assertCanDeleteIfPresent(legacyOutput, "legacy malformed-SAM output");

        File twoPassOutput = temp.resolve("malformed-two-pass-output.sam").toFile();
        expectRuntime(() -> deduplicateSamTwoPass(input.toFile(), twoPassOutput, 1));
        assertCanDeleteIfPresent(twoPassOutput, "two-pass malformed-SAM output");

        assertFileCanBeMovedAndReopened(input);
    }

    private static void testRepeatedMalformedInputDescriptorCleanup(Path temp) throws Exception{
        java.lang.management.OperatingSystemMXBean operatingSystem =
                ManagementFactory.getOperatingSystemMXBean();
        UnixOperatingSystemMXBean unixOperatingSystem =
                operatingSystem instanceof UnixOperatingSystemMXBean
                ? (UnixOperatingSystemMXBean)operatingSystem
                : null;
        Path malformedSam = temp.resolve("malformed.sam");
        Path malformedFastq = temp.resolve("malformed.fastq");

        // Exercise every failure path once before taking the baseline so class
        // loading and HTSJDK's one-time initialization cannot resemble a leak.
        runMalformedFailureRound(temp, malformedSam, malformedFastq);
        long before = unixOperatingSystem == null
                ? -1L
                : minimumOpenFileDescriptorCount(unixOperatingSystem);

        for(int i = 0; i < DESCRIPTOR_CLEANUP_REPETITIONS; i++)
            runMalformedFailureRound(temp, malformedSam, malformedFastq);

        if(unixOperatingSystem == null){
            System.out.println(
                    "Skipped open-file-descriptor count assertion: "
                    + "UnixOperatingSystemMXBean is unavailable"
            );
            return;
        }

        long after = minimumOpenFileDescriptorCount(unixOperatingSystem);

        if(after > before + DESCRIPTOR_NOISE_TOLERANCE){
            throw new AssertionError(
                    "repeated malformed direct-API calls leaked file descriptors: "
                    + "baseline=" + before + ", after=" + after
                    + ", tolerance=" + DESCRIPTOR_NOISE_TOLERANCE
                    + ", repetitions=" + DESCRIPTOR_CLEANUP_REPETITIONS
            );
        }
    }

    private static void runMalformedFailureRound(
            Path temp,
            Path malformedSam,
            Path malformedFastq) throws Exception{
        Path streamingOutput = temp.resolve("descriptor-streaming.sam");
        Path legacyOutput = temp.resolve("descriptor-legacy.sam");
        Path twoPassOutput = temp.resolve("descriptor-two-pass.sam");
        Path fastqOutput = temp.resolve("descriptor.fastq");

        Files.deleteIfExists(streamingOutput);
        expectRuntime(
                () -> deduplicateSam(
                        malformedSam.toFile(),
                        streamingOutput.toFile(),
                        1,
                        "on"
                )
        );
        Files.deleteIfExists(streamingOutput);

        Files.deleteIfExists(legacyOutput);
        expectRuntime(
                () -> deduplicateSam(
                        malformedSam.toFile(),
                        legacyOutput.toFile(),
                        1,
                        "off"
                )
        );
        Files.deleteIfExists(legacyOutput);

        Files.deleteIfExists(twoPassOutput);
        expectRuntime(
                () -> deduplicateSamTwoPass(
                        malformedSam.toFile(),
                        twoPassOutput.toFile(),
                        1
                )
        );
        Files.deleteIfExists(twoPassOutput);

        Files.deleteIfExists(fastqOutput);
        expectRuntime(
                () -> new DeduplicateFASTQ().deduplicateAndMerge(
                        malformedFastq.toFile(),
                        fastqOutput.toFile(),
                        new Directional(),
                        NgramBKTree.class,
                        new AnyMerge(),
                        0,
                        1,
                        0.5f,
                        false,
                        false
                )
        );
        Files.deleteIfExists(fastqOutput);

        try(Stream<Path> children = Files.list(temp)){
            if(children.anyMatch(path -> path.getFileName().toString().startsWith(".dumi-stream-")))
                throw new AssertionError("malformed direct-API call left a temporary output behind");
        }
    }

    private static long minimumOpenFileDescriptorCount(
            UnixOperatingSystemMXBean operatingSystem){
        long minimum = Long.MAX_VALUE;

        // A small minimum-of-samples window tolerates a transient JVM or test
        // harness descriptor without using sleeps or garbage collection.
        for(int i = 0; i < 5; i++){
            minimum = Math.min(minimum, operatingSystem.getOpenFileDescriptorCount());
            Thread.yield();
        }

        return minimum;
    }

    private static void testMalformedFastqCleanup(Path temp) throws Exception{
        Path input = temp.resolve("malformed.fastq");
        Files.write(
                input,
                Arrays.asList(
                        "@valid",
                        "AAAA",
                        "+",
                        "IIII",
                        "@broken",
                        "CCCC",
                        "+"
                ),
                StandardCharsets.US_ASCII
        );

        File output = temp.resolve("malformed-output.fastq").toFile();
        expectRuntime(
                () -> new DeduplicateFASTQ().deduplicateAndMerge(
                        input.toFile(),
                        output,
                        new Directional(),
                        NgramBKTree.class,
                        new AnyMerge(),
                        0,
                        1,
                        0.5f,
                        false,
                        false
                )
        );

        if(output.exists())
            throw new AssertionError("malformed FASTQ created an output before input validation completed");

        assertFileCanBeMovedAndReopened(input);
    }

    private static void testDataConstructionFailureIsFatal(Path temp) throws Exception{
        File input = writeSingleSam(temp.resolve("constructor.sam"));
        File output = temp.resolve("constructor-output.sam").toFile();

        try{
            new DeduplicateSAM().deduplicateAndMerge(
                    input,
                    output,
                    new Directional(),
                    UnconstructableData.class,
                    new AnyMerge(),
                    -1,
                    1,
                    0.5f,
                    false,
                    "_",
                    false,
                    false,
                    false,
                    false,
                    false,
                    "off"
            );
            throw new AssertionError("expected data-structure construction failure");
        }catch(IllegalStateException expected){
            if(expected.getMessage() == null
                    || !expected.getMessage().contains("Could not instantiate data structure")){
                throw new AssertionError("construction failure lacked a useful diagnostic", expected);
            }
        }

        if(!output.delete() && output.exists())
            throw new AssertionError("failed output could not be removed after resource cleanup");
    }

    private static void testExactFrequencyFailures(){
        ReadFreq readFreq = new ReadFreq(null, Integer.MAX_VALUE);
        expectArithmetic(readFreq::increment, "Read frequency");

        BitSet first = Utils.toBitSet("AAAA");
        BitSet second = Utils.toBitSet("AAAT");
        Map<BitSet, ReadFreq> reads = new LinkedHashMap<>();
        reads.put(first, new ReadFreq(null, Integer.MAX_VALUE));
        reads.put(second, new ReadFreq(null, 1));
        Set<BitSet> cluster = new LinkedHashSet<>(Arrays.asList(first, second));
        ClusterTracker tracker = new ClusterTracker(true);

        expectArithmetic(() -> tracker.addAll(cluster, reads), "Cluster frequency");
        expectArithmetic(
                () -> tracker.setOffset((long)Integer.MAX_VALUE + 1L),
                "Cluster ID offset"
        );
    }

    private static File writeSingleSam(Path path) throws Exception{
        Files.write(
                path,
                Arrays.asList(
                        "@HD\tVN:1.6\tSO:coordinate",
                        "@SQ\tSN:chr1\tLN:1000",
                        "single_AAAA\t0\tchr1\t1\t60\t4M\t*\t0\t0\tAAAA\tIIII"
                ),
                StandardCharsets.US_ASCII
        );
        return path.toFile();
    }

    private static File writeValidFastq(Path path) throws Exception{
        Files.write(
                path,
                Arrays.asList("@single", "AAAA", "+", "IIII"),
                StandardCharsets.US_ASCII
        );
        return path.toFile();
    }

    private static void deduplicateSam(File input, File output, int k, String streamingMode){
        deduplicateSam(input, output, -1, k, streamingMode);
    }

    private static void deduplicateSam(
            File input,
            File output,
            int umiLength,
            int k,
            String streamingMode){
        new DeduplicateSAM().deduplicateAndMerge(
                input,
                output,
                new Directional(),
                NgramBKTree.class,
                new AnyMerge(),
                umiLength,
                k,
                0.5f,
                false,
                "_",
                false,
                false,
                false,
                false,
                false,
                streamingMode
        );
    }

    private static void deduplicateSamTwoPass(File input, File output, int k){
        new DeduplicateSAM().deduplicateAndMergeTwoPass(
                input,
                output,
                new Directional(),
                NgramBKTree.class,
                new AnyMerge(),
                -1,
                k,
                0.5f,
                "_",
                false,
                false,
                false,
                false,
                false
        );
    }

    private static void deduplicateFastq(File input, File output){
        new DeduplicateFASTQ().deduplicateAndMerge(
                input,
                output,
                new Directional(),
                NgramBKTree.class,
                new AnyMerge(),
                0,
                1,
                0.5f,
                false,
                false
        );
    }

    private static void assertRecordCount(File file, int expected, String description) throws Exception{
        int actual = 0;

        try(SamReader reader = SamReaderFactory.makeDefault()
                    .validationStringency(ValidationStringency.STRICT)
                    .open(file);
                SAMRecordIterator records = reader.iterator()){
            while(records.hasNext()){
                records.next();
                actual++;
            }
        }

        if(actual != expected)
            throw new AssertionError(description + " emitted " + actual + " records");
    }

    private static void assertSortOrder(
            File file,
            SAMFileHeader.SortOrder expected,
            String description) throws Exception{
        try(SamReader reader = SamReaderFactory.makeDefault()
                    .validationStringency(ValidationStringency.STRICT)
                    .open(file)){
            SAMFileHeader.SortOrder actual = reader.getFileHeader().getSortOrder();

            if(actual != expected){
                throw new AssertionError(
                        description + " sort order was " + actual + ", expected " + expected
                );
            }
        }
    }

    private static void assertFileCanBeMovedAndReopened(Path input) throws Exception{
        Path moved = input.resolveSibling(input.getFileName().toString() + ".moved");
        Files.move(input, moved, StandardCopyOption.REPLACE_EXISTING);
        Files.move(moved, input, StandardCopyOption.REPLACE_EXISTING);

        if(Files.size(input) == 0L)
            throw new AssertionError("failed input could not be reopened after cleanup");
    }

    private static void assertCanDeleteIfPresent(File file, String description){
        if(file.exists() && !file.delete())
            throw new AssertionError(description + " could not be removed after resource cleanup");
    }

    private static void expectIllegalArgument(Runnable operation, String... messageParts){
        try{
            operation.run();
            throw new AssertionError("expected IllegalArgumentException");
        }catch(IllegalArgumentException expected){
            assertMessageContains(expected, messageParts);
        }
    }

    private static void expectArithmetic(Runnable operation, String... messageParts){
        try{
            operation.run();
            throw new AssertionError("expected ArithmeticException");
        }catch(ArithmeticException expected){
            assertMessageContains(expected, messageParts);
        }
    }

    private static void expectRuntime(Runnable operation){
        try{
            operation.run();
            throw new AssertionError("expected malformed input to fail");
        }catch(RuntimeException expected){
            // The concrete HTSJDK exception type is intentionally not part of
            // dUMI's public API; propagation and cleanup are the contract.
        }
    }

    private static void expectRuntimeMessage(Runnable operation, String... messageParts){
        try{
            operation.run();
            throw new AssertionError("expected operation to fail");
        }catch(RuntimeException expected){
            assertMessageContains(expected, messageParts);
        }
    }

    private static void assertMessageContains(RuntimeException exception, String... messageParts){
        for(String messagePart : messageParts){
            if(exception.getMessage() == null || !exception.getMessage().contains(messagePart)){
                throw new AssertionError(
                        "exception message omitted '" + messagePart + "': "
                        + exception.getMessage(),
                        exception
                );
            }
        }
    }

    private static void deleteRecursively(Path root) throws Exception{
        if(!Files.exists(root))
            return;

        try(Stream<Path> paths = Files.walk(root)){
            paths.sorted((left, right) -> right.compareTo(left))
                    .forEach(path -> {
                        try{
                            Files.deleteIfExists(path);
                        }catch(Exception ex){
                            throw new IllegalStateException("Could not delete " + path, ex);
                        }
                    });
        }
    }

    @FunctionalInterface
    private interface DirectDeduplicator{
        void run(File input, File output);
    }

    public static class UnconstructableData implements DataStructure{
        private UnconstructableData(){
        }

        @Override
        public void init(Map<BitSet, Integer> umiFreq, int umiLength, int maxEdits){
        }

        @Override
        public Set<BitSet> removeNear(BitSet umi, int k, int maxFreq){
            return new LinkedHashSet<>();
        }

        @Override
        public boolean contains(BitSet umi){
            return false;
        }

        @Override
        public Map<String, Float> stats(){
            return new LinkedHashMap<>();
        }
    }
}
