# Third-party notices

dUMI is distributed under the license in [`LICENSE`](LICENSE). Release
archives also contain the following unmodified third-party binary artifacts as
separate files under `lib/`. The dUMI build copies these JARs byte for byte; it
does not shade, unpack, patch, or recompile them.

| Artifact | SHA-256 | Upstream license summary |
| --- | --- | --- |
| `htsjdk-3.0.5.jar` | `8d03dc7672199f10fe4bad8aaf76259e36d15ed8fb145d6427ef1efb51a4da5f` | Mixed: primarily MIT, with Apache-2.0, LGPL-2.1, public-domain, and W3C-notice portions |
| `snappy-java-1.1.10.8.jar` | `50485d06037fea3d6e40c968386feeca6338cc9872e25549593ff3eb352cefcc` | Apache-2.0; includes Google Snappy under BSD-3-Clause and native runtime material covered by the GCC Runtime Library Exception |

The checksums above are the locked checksums in `dependencies.lock`.

## HTSJDK 3.0.5

HTSJDK's upstream licensing statement says that source-file and package notices
are authoritative. It describes most code as MIT-licensed, much of the CRAM
code as Apache-2.0, core Tribble code as LGPL-2.1, and SRA support code as an
uncopyrightable United States Government work. The distributed JAR also
contains `DateParser.class`, whose source carries the W3C IPR Software Notice.

The complete applicable texts and retained notices are:

- [`HTSJDK-3.0.5-MIT-NOTICES.txt`](third_party/licenses/HTSJDK-3.0.5-MIT-NOTICES.txt)
- [`HTSJDK-3.0.5-APACHE-NOTICES.txt`](third_party/licenses/HTSJDK-3.0.5-APACHE-NOTICES.txt)
- [`Apache-2.0.txt`](third_party/licenses/Apache-2.0.txt)
- [`HTSJDK-3.0.5-LGPL-NOTICES.txt`](third_party/licenses/HTSJDK-3.0.5-LGPL-NOTICES.txt)
- [`LGPL-2.1.txt`](third_party/licenses/LGPL-2.1.txt)
- [`HTSJDK-3.0.5-PUBLIC-DOMAIN-NOTICE.txt`](third_party/licenses/HTSJDK-3.0.5-PUBLIC-DOMAIN-NOTICE.txt)
- [`HTSJDK-3.0.5-W3C-IPR-SOFTWARE-NOTICE.txt`](third_party/licenses/HTSJDK-3.0.5-W3C-IPR-SOFTWARE-NOTICE.txt)

The HTSJDK JAR remains a separate, replaceable library in the release archive.
The exact corresponding source and build files are available from:

- Upstream tag `3.0.5`, commit
  [`8f8d5672f317bb1df03f85b3f8edea32c59e5542`](https://github.com/samtools/htsjdk/tree/8f8d5672f317bb1df03f85b3f8edea32c59e5542)
- [Upstream tag archive](https://github.com/samtools/htsjdk/archive/refs/tags/3.0.5.tar.gz)
- [Maven Central binary JAR](https://repo.maven.apache.org/maven2/com/github/samtools/htsjdk/3.0.5/htsjdk-3.0.5.jar)
- [Maven Central source JAR](https://repo.maven.apache.org/maven2/com/github/samtools/htsjdk/3.0.5/htsjdk-3.0.5-sources.jar)
- [Maven Central POM](https://repo.maven.apache.org/maven2/com/github/samtools/htsjdk/3.0.5/htsjdk-3.0.5.pom)

## snappy-java 1.1.10.8

snappy-java is licensed under Apache-2.0. Its upstream NOTICE attributes Google
Snappy under the New BSD License, Apache Hadoop's `PureJavaCrc32C` under
Apache-2.0, and statically linked `libstdc++` under the GCC Runtime Library
Exception. The unmodified upstream JAR includes platform-specific native
libraries.

The complete applicable texts and retained notices are:

- [`snappy-java-1.1.10.8-APACHE-NOTICES.txt`](third_party/licenses/snappy-java-1.1.10.8-APACHE-NOTICES.txt)
- [`Apache-2.0.txt`](third_party/licenses/Apache-2.0.txt)
- [`snappy-java-1.1.10.8-NOTICE.txt`](third_party/licenses/snappy-java-1.1.10.8-NOTICE.txt)
- [`Google-Snappy-BSD-3-Clause.txt`](third_party/licenses/Google-Snappy-BSD-3-Clause.txt)
- [`GCC-Runtime-Library-Exception-3.1.txt`](third_party/licenses/GCC-Runtime-Library-Exception-3.1.txt)

The exact corresponding source and build files are available from:

- Upstream tag `v1.1.10.8`, commit
  [`9bf8a09c7bce149f3f3e5f2d1e5a5be550b46aec`](https://github.com/xerial/snappy-java/tree/9bf8a09c7bce149f3f3e5f2d1e5a5be550b46aec)
- [Upstream tag archive](https://github.com/xerial/snappy-java/archive/refs/tags/v1.1.10.8.tar.gz)
- [Maven Central binary JAR](https://repo.maven.apache.org/maven2/org/xerial/snappy/snappy-java/1.1.10.8/snappy-java-1.1.10.8.jar)
- [Maven Central source JAR](https://repo.maven.apache.org/maven2/org/xerial/snappy/snappy-java/1.1.10.8/snappy-java-1.1.10.8-sources.jar)
- [Maven Central POM](https://repo.maven.apache.org/maven2/org/xerial/snappy/snappy-java/1.1.10.8/snappy-java-1.1.10.8.pom)

The notices in this file are provided for attribution and license compliance;
they do not change the license of dUMI or of any third-party component.
