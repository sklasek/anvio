import os
import argparse
import tempfile
import numpy as np
import pandas as pd

import anvio
import anvio.utils as utils
import anvio.hmmops as hmmops
import anvio.tables as tables
import anvio.fastalib as fastalib
import anvio.terminal as terminal
import anvio.filesnpaths as filesnpaths
import anvio.ccollections as ccollections

from itertools import combinations, product

from anvio.dbinfo import DBInfo
from anvio.errors import ConfigError
from anvio.drivers.blast import BLAST
from anvio.genomedescriptions import GenomeDescriptions


__author__ = "Developers of anvi'o (see AUTHORS.txt)"
__copyright__ = "Copyleft 2015-2022, the Meren Lab (http://merenlab.org/)"
__credits__ = []
__license__ = "GPL 3.0"
__maintainer__ = "Samuel Miller"
__email__ = "samuelmiller10@gmail.com"


progress = terminal.Progress()
run = terminal.Run()
progress = terminal.Progress()
run_quiet = terminal.Run(verbose=False)

pp = terminal.pretty_print

class SeedPermuter(object):
    """Using the `go` method, generates a FASTA file of permuted seed sequences from a tRNA-seq
    contigs database."""

    default_min_nt_frequency = 0.05
    default_max_variable_positions = 5

    def __init__(self, args={}, r=run, p=progress, do_sanity_check=True):
        self.args = args
        A = lambda x: args.__dict__[x] if x in args.__dict__ else None

        self.contigs_db_path = A('trnaseq_contigs_db')
        self.modifications_txt_path = A('modifications_txt')

        self.min_nt_frequency = A('min_nt_frequency')
        if self.min_nt_frequency is None:
            self.min_nt_frequency = self.default_min_nt_frequency
        self.max_variable_positions = A('max_variable_positions')
        if self.max_variable_positions is None:
            self.max_variable_positions = self.default_max_variable_positions

        # paths for output files
        self.permuted_seeds_fasta_path = A('permuted_seeds_fasta')
        self.tmp_dir = None
        if self.permuted_seeds_fasta_path is None:
            self.tmp_dir = anvio.TMP_DIR if anvio.TMP_DIR else tempfile.gettempdir()
            self.permuted_seeds_fasta_path = os.path.join(self.tmp_dir, "permuted_seeds.fa")

        self.run = r
        self.progress = p

        if do_sanity_check:
            self.sanity_check()

        self.contigs_db_info = DBInfo(self.contigs_db_path, expecting='contigs')


    def sanity_check(self):
        """Check the feasibility of args from initialization."""
        contigs_db_info = DBInfo(self.contigs_db_path, expecting='contigs')
        if contigs_db_info.variant != 'trnaseq':
            raise ConfigError(
                f"The database at '{self.contigs_db_path}' was a '{contigs_db_info.variant}' "
                "variant, not the required 'trnaseq' variant.")

        filesnpaths.is_file_exists(self.modifications_txt_path)

        if not (0 <= self.min_nt_frequency < 1):
            raise ConfigError(
                "The specified minimum nucleotide frequency for permutation is "
                f"'{self.min_nt_frequency}', which is not in the required range [0, 1).")

        if self.max_variable_positions < 1:
            raise ConfigError(
                "The specified maximum number of variable positions in a permuted sequence is "
                f"'{self.max_variable_positions}', but it needs to be a positive integer.")

        filesnpaths.is_output_file_writable(self.permuted_seeds_fasta_path)


    def go(self):
        """Permute tRNA seed sequences at predicted sites of modification-induced substitutions."""
        with self.contigs_db_info.load_db() as contigs_db:
            seed_contig_names_seqs = contigs_db.get_table_as_list_of_tuples('contig_sequences')

        variable_nts_gb = self.get_variable_nts_gb()

        output_fasta = fastalib.FastaOutput(self.permuted_seeds_fasta_path)
        self.run.info("FASTA file of permuted seeds", self.permuted_seeds_fasta_path)

        # Permute tRNA-seq seeds with predicted modifications.
        # Write all seed sequences to the output FASTA along with permuted sequences.
        seed_count = 0
        seed_total = len(seed_contig_names_seqs)
        permuted_seq_count = 0
        max_permuted_seq_count = 0
        self.progress.new("Permuting seed sequences", progress_total_items=seed_total)
        for seed_contig_name, seed_seq in seed_contig_names_seqs:
            self.progress.update(f"{seed_count}/{seed_total}")
            seed_count += 1
            self.progress.increment()

            try:
                variable_nts_df = variable_nts_gb.get_group(seed_contig_name)
            except KeyError:
                # No modified positions were predicted in the seed.
                output_fasta.write_id(seed_contig_name)
                output_fasta.write_seq(seed_seq)
                continue

            variable_nts_dict = self.get_variable_nts_dict(variable_nts_df, seed_seq)
            if not variable_nts_dict:
                # No nucleotides beside the most abundant had relative frequencies above the minimum
                # threshold for permutation at any predicted modification positions in the seed.
                output_fasta.write_id(seed_contig_name)
                output_fasta.write_seq(seed_seq)
                continue

            permuted_seed_info = self.get_permuted_seed_info(variable_nts_dict, seed_seq)

            permuted_seq_count += len(permuted_seed_info)
            max_permuted_seq_count = max(len(permuted_seed_info), max_permuted_seq_count)

            output_fasta.write_id(seed_contig_name)
            output_fasta.write_seq(seed_seq)
            for permuted_seq, permuted_positions, permuted_nts in permuted_seed_info:
                permutations = [
                    str(permuted_position) + permuted_nt
                    for permuted_position, permuted_nt in zip(permuted_positions, permuted_nts)]
                seq_id = f"{seed_contig_name}|{'_'.join(permutations)}"
                output_fasta.write_id(seq_id)
                output_fasta.write_seq(permuted_seq)
        self.progress.end()

        self.run.info("Mean permuted seqs per seed", round(permuted_seq_count / seed_total, 1))
        self.run.info("Max permuted seqs from a seed", pp(max_permuted_seq_count))


    def get_variable_nts_gb(self):
        """Load nucleotide variability data for modification-induced substitutions.

        Load the modifications table (generated by `anvi-tabulate-trnaseq`) and drop rows for
        samples lacking reported variability for the predicted modification.

        Returns
        =======
        variable_nts_gb : pandas DataFrameGroupBy
            the modifications table with a subset of columns grouped by seed ID ("contig name")
        """
        modifications_df = pd.read_csv(self.modifications_txt_path, sep='\t', header=0,
                                       usecols=['contig_name', 'seed_position', 'A', 'C', 'G', 'T'])
        modifications_df = modifications_df.set_index('contig_name')
        modifications_df = modifications_df.dropna()
        variable_nts_gb = modifications_df.groupby('contig_name')

        return variable_nts_gb


    def get_variable_nts_dict(self, variable_nts_df, seed_seq):
        """Find nucleotides that can be substituted at each predicted modified position in a seed.

        Each row in the input table contains data for a single seed + predicted modified position +
        sample. Samples without a predicted modified position due to a lack of variability (as found
        in `anvi-merge-trnaseq`) are not present in this table.

        For each seed + modified position, calculate the average relative frequency of each nucleotide
        across samples. Ignore the nucleotide in the seed sequence.

        Record the nucleotides meeting the relative frequency threshold.

        Parameters
        ==========
        variable_nts_df : pandas DataFrame
            a table of nucleotide frequencies at predicted modified positions in a seed

        seed_seq : str
            the seed sequence to permute

        Returns
        =======
        variable_nts_dict : dict
            a dict with keys being predicted modified positions in the seed and values being tuples
            of minority nucleotides (chars)
        """
        # Loop through each predicted modification in the seed.
        variable_nts_dict = {}
        for position, gb_df in variable_nts_df.set_index('seed_position').groupby(
            'seed_position', sort=False):
            # Calculate nucleotide relative frequencies for each sample.
            position_variable_nts_df = gb_df.div(gb_df.sum(axis=1), axis=0)
            # Drop the column of the relative frequency of the nucleotide in the seed sequence.
            position_variable_nts_df = position_variable_nts_df.drop(seed_seq[position], axis=1)
            # Calculate the mean relative frequency of each nucleotide across samples.
            position_variable_nts_series = position_variable_nts_df.mean(axis=0)
            # Filter nucleotides by threshold relative frequency.
            position_variable_nts_series = position_variable_nts_series[
                position_variable_nts_series > self.min_nt_frequency]

            if len(position_variable_nts_series) == 0:
                continue

            variable_nts_dict[position] = tuple(position_variable_nts_series.index)
        return variable_nts_dict


    def get_permuted_seed_info(self, variable_nts_dict, seed_seq):
        """Generate permuted sequences, returning not just the new sequences but also the permuted
        positions and substituted nucleotides in the sequences.

        Parameters
        ==========
        variable_nts_dict : dict
            the dict recording nucleotides to substitute at positions in the seed

        seed_seq : str
            the seed sequence to permute

        Returns
        =======
        permuted_seed_info : list
            a list of tuples, each tuple containing a 1) permuted seed sequence string, 2) a tuple
            of permuted position indices, and 3) a tuple of substituted nucleotide characters at
            those positions
        """
        permuted_seed_info = []
        # Loop through the different numbers of permuted positions that can be introduced in the
        # sequence, starting with 1 permuted position.
        for num_variable_positions in range(
            1, min(len(variable_nts_dict), self.max_variable_positions) + 1):
            # Find combinations of permuted positions, e.g., with 1 permuted position, and positions
            # 8 and 32 being variable, then one combination would simply be (8, ) and the other
            # combination (32, ).
            permutation_combinations = combinations(variable_nts_dict, num_variable_positions)
            # Loop through each combination.
            for permuted_positions in permutation_combinations:
                # Find the sets of nucleotides that will be substituted into the sequence given the
                # combination of positions. Each loop generates a new permuted sequence and entry.
                for permuted_nts in product(
                    *[variable_nts_dict[position] for position in permuted_positions]):
                    permuted_seed_seq = seed_seq
                    # Loop through the positions to make the nucleotide substitutions.
                    for position, nt in zip(permuted_positions, permuted_nts):
                        permuted_seed_seq = \
                            permuted_seed_seq[: position] + nt + permuted_seed_seq[position + 1: ]
                    # In addition to the permuted sequence, record the permuted positions and
                    # substituted nucleotides.
                    permuted_seed_info.append(
                        (permuted_seed_seq, tuple(permuted_positions), tuple(permuted_nts)))

        return permuted_seed_info


class Integrator(object):
    """Using the `go` method, links tRNA-seq seeds to tRNA genes and adds this information to the
    tRNA-seq contigs database."""
    # Here are the different possible (meta)genomic sources:
    # 1. Single contigs database without bins
    # 2. Single contigs database with collection of bins
    # 3. Single contigs database with specified bin
    # 4. One or more contigs databases input as "external" genomes
    # 5. "Internal" genomes (bins) from one or more contigs databases
    # 6. A combination of "internal" and "external" genomes (4 + 5)
    # Ambiguous assignment of tRNA-seq seeds to tRNA genes can be applied to all but (1).

    default_max_mismatches = 3
    blast_search_output_cols = [
        'qseqid', 'sseqid', 'mismatch', 'qstart', 'qlen', 'sstart', 'send', 'slen', 'bitscore']

    def __init__(self, args={}, p=progress, r=run, do_sanity_check=True):
        self.args = args
        A = lambda x: args.__dict__[x] if x in args.__dict__ else None

        self.trnaseq_contigs_db_path = A('trnaseq_contigs_db')
        self.seeds_specific_txt_path = A('seeds_specific_txt')
        self.modifications_txt_path = A('modifications_txt')

        self.genomic_contigs_db_path = A('contigs_db')
        self.genomic_profile_db_path = A('profile_db')
        self.collection_name = A('collection_name')
        self.bin_id = A('bin_id')

        self.internal_genomes_path = A('internal_genomes')
        self.external_genomes_path = A('external_genomes')

        self.max_mismatches = A('max_mismatches')
        if self.max_mismatches is None:
            self.max_mismatches = self.default_max_mismatches
        self.full_gene = A('full_gene')
        if self.full_gene is None:
            self.full_gene = False
        self.ambiguous_genome_assignment = A('ambiguous_genome_assignment')
        if self.ambiguous_genome_assignment is None:
            self.ambiguous_genome_assignment = False

        self.permuted_seeds_fasta_path = A('permuted_seeds_fasta')
        self.blast_dir = A('blast_dir')
        if self.blast_dir is None:
            self.blast_dir = anvio.TMP_DIR if anvio.TMP_DIR else tempfile.gettempdir()

        self.num_threads = A('num_threads') or anvio.K('num_threads')['default']
        self.remove_previous_matches = A('remove_previous_matches')

        self.trna_genes_fasta_path = os.path.join(self.blast_dir, 'trna_genes.fa')

        self.run = r
        self.progress = p

        self.trnaseq_contigs_db_info = DBInfo(self.trnaseq_contigs_db_path)

        # Store information on accessing (meta)genomes. The entries in the dictionary differ given
        # what was passed in `args`.
        # 1. Single contigs database without bins: entry represents a contigs database.
        # 2. Single contigs database with collection of bins: entry represents a collection.
        # 3. Single contigs database with specified bin: entry represents a bin.
        # 4. One or more contigs databases input as "external" genomes: entries represent contigs
        #    databases.
        # 5. "Internal" genomes (bins) from one or more contigs databases: entries represent bins.
        # 6. A combination of "internal" and "external" genomes (4 + 5)`: entries represent contigs
        #    databases or bins.
        self.genome_info_dict = {}

        # If `contigs_db` was provided, then `internal_genomes` and `external_genomes` should not
        # also have been provided, which is checked later in `sanity_check`.
        if self.genomic_contigs_db_path:
            contigs_db_info = DBInfo(self.genomic_contigs_db_path, expecting='contigs')
            self.genome_info_dict[contigs_db_info.project_name] = genome_info = {}
            genome_info['contigs_db_info'] = contigs_db_info
            if self.genomic_profile_db_path:
                genome_info['profile_db_info'] = DBInfo(
                    self.genomic_profile_db_path, expecting='profile')
            else:
                genome_info['profile_db_info'] = None
            genome_info['collection_name'] = self.collection_name
            genome_info['bin_id'] = self.bin_id

        if self.internal_genomes_path or self.external_genomes_path:
            descriptions = GenomeDescriptions(args, run=run_quiet, progress=self.progress)
            descriptions.load_genomes_descriptions(init=False)

            for genome_name, genome_dict in descriptions.internal_genomes_dict.items():
                self.genome_info_dict[genome_name] = genome_info = {}
                genome_info['contigs_db_info'] = DBInfo(
                    genome_dict['contigs_db_path'], expecting='contigs')
                if genome_dict['profile_db_path']:
                    genome_info['profile_db_info'] = DBInfo(
                        genome_dict['profile_db_path'], expecting='profile')
                else:
                    genome_info['profile_db_info'] = None
                genome_info['collection_name'] = genome_dict['collection_id']
                genome_info['bin_id'] = genome_dict['bin_id']

            for genome_name, genome_dict in descriptions.external_genomes_dict.items():
                self.genome_info_dict[genome_name] = genome_info = {}
                genome_info['contigs_db_info'] = DBInfo(
                    genome_dict['contigs_db_path'], expecting='contigs')
                if genome_dict['profile_db_path']:
                    genome_info['profile_db_info'] = DBInfo(
                        genome_dict['profile_db_path'], expecting='profile')
                else:
                    genome_info['profile_db_info'] = None
                genome_info['collection_name'] = None
                genome_info['bin_id'] = None

        if do_sanity_check:
            self.sanity_check()


    def sanity_check(self, check_permuted_seeds_fasta=False):
        """Check the feasibility of args from initialization."""
        trnaseq_contigs_db_info = DBInfo(self.trnaseq_contigs_db_path, expecting='contigs')
        if trnaseq_contigs_db_info.variant != 'trnaseq':
            raise ConfigError(
                f"The database at '{self.trnaseq_contigs_db_path}' was a "
                f"'{trnaseq_contigs_db_info.variant}' variant, not the required 'trnaseq' variant.")

        # Existing seed/gene hits must be willfully overwritten or appended to.
        hit_count = trnaseq_contigs_db_info.load_db().get_row_counts_from_table(
            tables.trna_gene_hits_table_name)
        if hit_count:
            self.run.info(
                "Preexisting tRNA-seq seed/tRNA gene hits in the tRNA-seq contigs db", hit_count)
            if not self.ambiguous_genome_assignment and not self.remove_previous_matches:
                raise ConfigError(
                    "The seeds from the tRNA-seq contigs database at "
                    f"'{self.trnaseq_contigs_db_path}' have already been associated with tRNA "
                    "genes from one or more (meta)genomic contigs databases. Either use "
                    "`remove_previous_matches` to clear existing matches, or append to existing "
                    "matches with `ambiguous_genome_assignments`.")

        # The tRNA-seq contigs db version must be up-to-date.
        required_version = utils.get_required_version_for_db(self.trnaseq_contigs_db_path)
        if str(trnaseq_contigs_db_info.version) != required_version:
            raise ConfigError(
                f"The database at '{self.trnaseq_contigs_db_path}' is outdated (this database is "
                f"v{trnaseq_contigs_db_info.version} and your anvi'o installation wants to work "
                f"with v{required_version}). You can migrate your database without losing any data "
                "using the program `anvi-migrate` with either of the flags `--migrate-dbs-safely` "
                "or `--migrate-dbs-quickly`.")

        # Right now there are no specific checks here on the format of these tables.
        filesnpaths.is_file_exists(self.seeds_specific_txt_path)
        filesnpaths.is_file_exists(self.modifications_txt_path)

        # Do basic checks of the combinations of genomic input arguments.
        if (self.genomic_contigs_db_path and
            (self.internal_genomes_path or self.external_genomes_path)):
            raise ConfigError("`internal_genomes` and `external_genomes` cannot be used with "
                              "`contigs_db`.")

        if ((self.genomic_profile_db_path or self.collection_name) and
            not (self.genomic_contigs_db_path and
                 self.genomic_profile_db_path and
                 self.collection_name)):
            raise ConfigError("A collection must be provided using `contigs_db`, `profile_db`, and "
                              "`collection_name`.")

        if (self.bin_id and
            not (self.genomic_contigs_db_path and
                 self.genomic_profile_db_path and
                 self.collection_name)):
            raise ConfigError("A specific bin provided with `bin_id` also requires `contigs_db`, "
                              "`profile_db`, and `collection_name`.")

        # Prevent a confused user from providing a tRNA-seq contigs database in lieu of a
        # (meta)genomic contigs database.
        unrecognized = []
        for name, genome_info in self.genome_info_dict.items():
            if genome_info['contigs_db_info'].variant != 'unknown':
                unrecognized.append(name)
        if unrecognized:
            if self.genomic_contigs_db_path:
                raise ConfigError(
                    f"The purported (meta)genomic contigs database, '{unrecognized[0]}', was not "
                    "recognized as such. A proper database (technically of the 'unknown' variant) "
                    "should be generated by `anvi-gen-contigs-database`.")
            else:
                raise ConfigError(
                    "The purported (meta)genomic contigs databases for the following genomes were "
                    "not recognized as such. A proper database (technically of the 'unknown' "
                    "variant) should be generated by `anvi-gen-contigs-database`. "
                    f"{', '.join(unrecognized)}")

        # Check that there are tRNA genes annotated in the (meta)genomes.
        unannotated = []
        for name, genome_info in self.genome_info_dict.items():
            if 'Transfer_RNAs' not in hmmops.SequencesForHMMHits(
                genome_info['contigs_db_info'].path).hmm_hits_info:
                unannotated.append(name)
        if unannotated:
            if self.genomic_contigs_db_path:
                raise ConfigError(
                    "It appears that tRNA genes have not been annotated in the (meta)genomic "
                    f"contigs database, '{unannotated[0]}'. Please run `anvi-scan-trnas` on the "
                    "database and try again (this same error will arise if no tRNA genes are "
                    "found).")
            else:
                raise ConfigError(
                    "It appears that tRNA genes have not been annotated in the following "
                    "genomes. Please run `anvi-scan-trnas` on the contigs databases and try again "
                    "(this same error will arise if no tRNA genes are found). "
                    f"{', '.join(unannotated)}.")

        # Check that profile databases correspond to (meta)genomic contigs databases.
        incompatible = []
        for name, genome_info in self.genome_info_dict.items():
            if genome_info['profile_db_info']:
                try:
                    utils.is_profile_db_and_contigs_db_compatible(
                        genome_info['profile_db_info'].path, genome_info['contigs_db_info'].path)
                except ConfigError:
                    incompatible.append(name)
        if incompatible:
            if self.genomic_contigs_db_path:
                raise ConfigError(
                    f"The (meta)genomic contigs database, '{incompatible[0]}', is not compatible "
                    f"with the provided profile database, {genome_info['profile_db_info'].path}. "
                    "In fact, the profile database was not generated from the contigs database.")
            else:
                raise ConfigError(
                    "The contigs databases for the following genomes are not compatible with the "
                    "corresponding profile databases. In fact, the profile databases were not "
                    f"generated from the contigs databases. {', '.join(incompatible)}")

        # Check putative collections.
        unrecognized = []
        for name, genome_info in self.genome_info_dict.items():
            if genome_info['profile_db_info']:
                collections = ccollections.Collections()
                collections.populate_collections_dict(genome_info['profile_db_info'].path)
                if genome_info['collection_name'] not in collections.collections_dict:
                    unrecognized.append(name)
        if unrecognized:
            if self.genomic_contigs_db_path:
                raise ConfigError(
                    f"The profile database, '{self.genomic_profile_db_path}', does not contain the "
                    f"requested collection, '{self.collection_name}'.")
            else:
                raise ConfigError(
                    "The profile databases do not contain the requested collections for the "
                    f"following genomes. {', '.join(unrecognized)}")

        if self.max_mismatches < 0:
            raise ConfigError(
                "The maximum number of mismatches allowed in a seed-gene alignment must be a "
                f"non-negative integer, not the provided value of {self.max_mismatches}")

        filesnpaths.is_output_dir_writable(self.blast_dir)

        # Ignore this sanity check when using `genomictrnaseq.Permuter` FASTA output.
        if check_permuted_seeds_fasta:
            filesnpaths.is_file_fasta_formatted(self.permuted_seeds_fasta_path)

        if self.num_threads < 1:
            raise ConfigError("The number of threads (used by BLAST) must be a positive integer, "
                              f"not the provided value of {self.num_threads}")


    def go(self):
        """Link tRNA-seq seeds to tRNA genes, adding this information to the tRNA-seq contigs
        database."""
        trna_gene_seq_dict = self.write_trna_genes_fasta()
        self.blast()
        hits_df = self.filter_hits(trna_gene_seq_dict)
        unmodified_nt_df = self.find_unmodified_nucleotides(hits_df)
        self.update_trnaseq_contigs_database(hits_df, unmodified_nt_df)


    def write_trna_genes_fasta(self):
        """
        Write all tRNA gene sequences from input contigs databases to a FASTA file.

        Returns
        =======
        trna_gene_seq_dict : dict
            tRNA gene sequences keyed by tuple of contigs database name and gene callers ID.
        """
        trna_genes_fasta = open(self.trna_genes_fasta_path, 'w')

        # Unfortunately, the full subject (tRNA gene) sequence cannot be reported in the BLAST
        # output table, but these sequences are needed to filter alignments, so they are filed in a
        # dictionary.
        trna_gene_seq_dict = {}

        # Get the unique set of input contigs databases.
        contigs_db_paths = set()
        contigs_db_infos = []
        for genome_info in self.genome_info_dict.values():
            if genome_info['contigs_db_info'].path in contigs_db_paths:
                continue
            contigs_db_infos.append(genome_info['contigs_db_info'])

        for contigs_db_info in contigs_db_infos:
            contigs_db_project_name = contigs_db_info.project_name
            contigs_db_hash = contigs_db_info.hash

            trna_gene_info = hmmops.SequencesForHMMHits(
                contigs_db_info.path, sources=set(['Transfer_RNAs']))

            # Split names from the database are needed here to recover the tRNA sequence strings.
            splits_dict = {
                contigs_db_hash: list(contigs_db_info.load_db().smart_get(
                    tables.splits_info_table_name, 'split').keys())}
            hmm_seqs_dict = trna_gene_info.get_sequences_dict_for_hmm_hits_in_splits(splits_dict)

            for gene_id, gene_entry in hmm_seqs_dict.items():
                seq_string = hmm_seqs_dict[gene_id]['sequence']

                # Record both the project name and hash of the contigs database: the project name is
                # not guaranteed to be unique.
                header = (f"{contigs_db_project_name}|"
                          f"{contigs_db_hash}|"
                          f"{gene_entry['contig']}|"
                          f"{gene_entry['gene_callers_id']}|"
                          f"{gene_entry['gene_name']}|"
                          f"{gene_entry['e_value']}|"
                          f"{gene_entry['start']}|"
                          f"{gene_entry['stop']}")
                trna_genes_fasta.write(f">{header}\n")
                trna_genes_fasta.write(f"{seq_string}\n")

                trna_gene_seq_dict[(contigs_db_hash, gene_entry['gene_callers_id'])] = seq_string

        trna_genes_fasta.close()

        return trna_gene_seq_dict


    def blast(self):
        """Align tRNA-seq seeds/permuted seeds to tRNA genes."""
        blast = BLAST(self.permuted_seeds_fasta_path,
                      self.trna_genes_fasta_path,
                      search_program='blastn',
                      run=self.run,
                      progress=self.progress,
                      num_threads=self.num_threads)
        blast.tmp_dir = self.blast_dir
        blast.search_output_path = os.path.join(self.blast_dir, 'blast-search-results.txt')
        blast.log_file_path = os.path.join(self.blast_dir, 'blast-log.txt')
        blast.additional_params_for_blast = "-ungapped"
        blast.makedb(dbtype='nucl')
        blast.blast(outputfmt='6 ' + ' '.join(self.blast_search_output_cols))


    def filter_hits(self, trna_gene_seq_dict):
        """
        Confidently associate tRNA-seq seeds with tRNA genes, filtering BLAST alignments of
        seeds/permuted seeds to genes.

        Parameters
        ==========
        trna_gene_seq_dict : dict
            tRNA gene sequences used as subjects in BLAST search keyed by tuple of contigs database
            name and gene callers ID.

        Returns
        =======
        hits_df : pandas.core.frame.DataFrame
            Each row contains a selected hit between a seed, which may be permuted, and tRNA gene.
        """
        # Load BLAST output table.
        search_output_path = os.path.join(self.blast_dir, 'blast-search-results.txt')
        hits_df = pd.read_csv(
            search_output_path, sep='\t', header=None, names=self.blast_search_output_cols)

        # Discard alignments with too many mismatches.
        hits_df = hits_df[hits_df['mismatch'] <= self.max_mismatches]
        if self.full_gene:
            # Discard alignments that do not start at the beginning of the gene.
            hits_df = hits_df[hits_df['sstart'] == 1]

        # Discard (enigmatic) indistinguishable, duplicate hits if they exist.
        hits_df = hits_df.drop_duplicates()

        # Parse seed IDs and permutation info.
        hits_df[['seed_contig_name', 'seed_permutation']] = \
            hits_df['qseqid'].str.split('|', expand=True)
        hits_df['seed_permutation'] = hits_df['seed_permutation'].fillna('')
        hits_df = hits_df.drop('qseqid', axis=1)

        # Extract information on each hit.
        contigs_db_project_names = []
        contigs_db_hashes = []
        gene_contig_names = []
        gene_callers_ids = []
        decoded_amino_acids = []
        anticodons = []
        trnascan_scores = []
        gene_starts = []
        gene_stops = []
        gene_sequences = []
        for sseqid in hits_df['sseqid']:
            split_sseqid = sseqid.split('|')
            contigs_db_project_name = split_sseqid[0]
            contigs_db_project_names.append(contigs_db_project_name)
            contigs_db_hash = split_sseqid[1]
            contigs_db_hashes.append(contigs_db_hash)
            gene_contig_names.append(split_sseqid[2])
            gene_callers_id = int(split_sseqid[3])
            gene_callers_ids.append(gene_callers_id)
            gene_name = split_sseqid[4]
            decoded_amino_acid, anticodon = gene_name.split('_')[: 2]
            decoded_amino_acids.append(decoded_amino_acid)
            anticodons.append(anticodon)
            trnascan_scores.append(float(split_sseqid[5]))
            gene_starts.append(int(split_sseqid[6]))
            gene_stops.append(int(split_sseqid[7]))
            gene_sequences.append(trna_gene_seq_dict[(contigs_db_hash, gene_callers_id)])
        hits_df['contigs_db_project_name'] = contigs_db_project_names
        hits_df['contigs_db_hash'] = contigs_db_hashes
        hits_df['gene_contig_name'] = gene_contig_names
        hits_df['decoded_amino_acid'] = decoded_amino_acids
        hits_df['anticodon'] = anticodons
        hits_df['trnascan_score'] = trnascan_scores
        hits_df['gene_callers_id'] = gene_callers_ids
        hits_df['gene_start_in_contig'] = gene_starts
        hits_df['gene_stop_in_contig'] = gene_stops
        hits_df['gene_sequence'] = gene_sequences

        # Filter individual alignments.
        retained_indices = []
        for (index,
             decoded_amino_acid,
             seed_alignment_start,
             seed_length,
             gene_alignment_start,
             gene_alignment_end,
             gene_sequence,
             gene_length) in zip(hits_df.index,
                                 hits_df['decoded_amino_acid'],
                                 hits_df['qstart'],
                                 hits_df['qlen'],
                                 hits_df['sstart'],
                                 hits_df['send'],
                                 hits_df['gene_sequence'],
                                 hits_df['slen']):
            if ((gene_length - gene_alignment_end == 0) or
                ((gene_length - gene_alignment_end == 3) and gene_sequence[-3: ] == 'CCA')):
                # The alignment ends at the end of the gene or just short of a 3'-CCA acceptor in
                # the gene (the seed/permuted seed should never contain the 3'-CCA acceptor).
                if ((seed_alignment_start == 1) and
                    (gene_alignment_end - gene_alignment_start == seed_length - 1)):
                    # The alignment starts at the beginning of the seed/permuted seed and spans the
                    # entire query.
                    retained_indices.append(index)
                elif ((decoded_amino_acid == 'His') and
                      (seed_alignment_start == 2) and
                      (gene_alignment_end - gene_alignment_start == seed_length - 2)):
                    # The alignment starts at the second position of the tRNA-His seed/permuted seed
                    # sequence, which has a post-transcriptional G at the 5' end, and spans the
                    # remaining length of the query.
                    retained_indices.append(index)
        hits_df = hits_df.loc[retained_indices]

        # Retain each seed/permuted seed's top-scoring hits.
        hits_df = hits_df[
            hits_df.groupby('seed_contig_name')['bitscore'].transform('max') == hits_df['bitscore']]

        ##################################################
        # Now add information on internal genomes of interest that contain the tRNA genes. Note that
        # external genomes derived from the same metagenome can contain the same tRNA gene: hits to
        # the same gene in different external genomes are distinguished by contigs database project
        # name/hash.
        # Create a dictionary, `contig_bin_dict`, mapping the names of contigs bearing tRNA genes to
        # bin info.
        contig_bin_dict = {}
        for genome_info in self.genome_info_dict.values():
            if not genome_info['bin_id'] and not genome_info['collection_name']:
                continue

            args = argparse.ArgumentParser()
            if genome_info['bin_id']:
                args.contigs_db = genome_info['contigs_db_info'].path
                args.profile_db = genome_info['profile_db_info'].path
                args.collection_name = collection_name = genome_info['collection_name']
                args.bin_id = genome_info['bin_id']
                search_for_bin_of_interest = True
            elif genome_info['collection_name']:
                # A single collection was supplied in the input arguments, `self.collection_name`.
                args.contigs_db = genome_info['contigs_db_info'].path
                args.profile_db = genome_info['profile_db_info'].path
                args.collection_name = collection_name = genome_info['collection_name']
                search_for_bin_of_interest = True
            else:
                search_for_bin_of_interest = False

            # Note that the same contig may be in different bins, thus the list values of the dict.
            contigs_db_hash = genome_info['contigs_db_info'].hash
            profile_db_sample_id = genome_info['profile_db_info'].sample_id
            if search_for_bin_of_interest:
                bin_contig_names_dict = ccollections.GetSplitNamesInBins(args).get_dict()
                for bin_id, split_names in bin_contig_names_dict.items():
                    for split_name in split_names:
                        contig_name = split_name.split('_split_')[0]
                        try:
                            contig_bin_dict[(contigs_db_hash, contig_name)].append(
                                (profile_db_sample_id, collection_name, bin_id))
                        except KeyError:
                            contig_bin_dict[(contigs_db_hash, contig_name)] = [
                                (profile_db_sample_id, collection_name, bin_id)]

        # Make a table of the membership of gene-bearing contigs in contigs databases/bins.
        contig_bin_rows = []
        for row in hits_df[
            ['contigs_db_hash', 'gene_contig_name']].drop_duplicates().itertuples(index=False):
            contigs_db_hash = row.contigs_db_hash
            contig_name = row.gene_contig_name
            try:
                bin_info = contig_bin_dict[(contigs_db_hash, contig_name)]
            except KeyError:
                # The contig is not binned.
                contig_bin_rows.append([contigs_db_hash, contig_name, '', '', ''])
                continue
            for profile_db_sample_id, collection_name, bin_id in bin_info:
                # Record each bin containing the contig.
                contig_bin_rows.append(
                    [contigs_db_hash, contig_name, profile_db_sample_id, collection_name, bin_id])
        contig_bin_df = pd.DataFrame(contig_bin_rows,
                                     columns=['contigs_db_hash',
                                              'gene_contig_name',
                                              'profile_db_sample_id',
                                              'collection_name',
                                              'bin_id'])
        # Merge the table of seed/gene hits with the new table of bin membership, multiplying each
        # row per hit by each bin containing the gene.
        hits_df = hits_df.merge(contig_bin_df, on=['contigs_db_hash', 'gene_contig_name'])

        # For the sake of clarity, here is what happens to each of the different possible
        # (meta)genomic sources when "ambiguous" tRNA gene assignment is NOT allowed.
        # 1. Single contigs database without bins: The existence of genomes is not assumed, so no
        #    hits are disregarded.
        # 2. Single contigs database with collection of bins: disregard seeds with equally strong
        #    hits that are not confined to a single bin.
        # 3. Single contigs database with specified bin: disregard seeds with equally strong hits
        #    that are not confined to the bin.
        # 4. One or more contigs databases input as "external" genomes: disregard seeds with equally
        #    strong hits that are not confined to a single contigs database.
        # 5. "Internal" genomes (bins) from one or more contigs databases: disregard seeds with
        #    equally strong hits that are not confined to a single bin.
        # 6. A combination of "internal" and "external" genomes (4 + 5): disregard seeds with
        #    equally strong hits that are not confined to a single internal genome bin or external
        #    genome contigs database.
        if self.genomic_contigs_db_path and not self.collection_name and not self.bin_id: # (1)
            is_simple_contigs_db_input = True
        else:
            is_simple_contigs_db_input = False
        if not self.ambiguous_genome_assignment and not is_simple_contigs_db_input:
            # Drop hits to genes in multiple bins: partly takes care of (2), (5), and (6).
            hits_df = hits_df.groupby('seed_contig_name').filter(
                lambda seed_df: len(seed_df[['contigs_db_hash',
                                             'profile_db_sample_id',
                                             'collection_name',
                                             'bin_id']].drop_duplicates()) == 1)
            # Drop hits to genes in multiple contigs databases: takes care of (4), partly takes care
            # of (5) and (6).
            hits_df = hits_df.groupby('seed_contig_name').filter(
                lambda seed_df: len(seed_df['contigs_db_hash'].drop_duplicates()) == 1)
            # Drop hits to genes in unbinned contigs: takes care of (3), finishes taking care of
            # (2), (5), and (6).
            if (self.collection_name or
                (self.internal_genomes_path and not self.external_genomes_path)):
                hits_df = hits_df[hits_df['bin_id'] != '']
            elif self.internal_genomes_path and self.external_genomes_path:
                external_genome_contigs_db_hashes = [
                    genome_info['contigs_db_hash'] for genome_info in self.genome_info_dict.values()
                    if genome_info['bin_id'] is None]
                retained_index = []
                for key, contigs_db_hash, bin_id in zip(hits_df.index, hits_df['contigs_db_hash'], hits_df['bin_id']):
                    if contigs_db_hash in external_genome_contigs_db_hashes:
                        retained_index.append(key)
                        continue
                    if bin_id is not None:
                        retained_index.append(key)
                hits_df = hits_df.loc[retained_index]
        ##################################################

        # Multiple permutations of the same seed may be retained after filtering by score. There are
        # two and possibly more ways that this can occur. (1) The unmodified nucleotide at a
        # modified position has a very low frequency and so was not used in the permuted sequences.
        # The permuted sequences, none of which contain the correct nucleotide, mismatch this
        # nucleotide in genes equally well. (2) A permutation is introduced at a predicted
        # modification position that is actually a nucleotide variant, and different versions of the
        # variant occur in different (meta)genomic contigs. The following procedure resolves both of
        # these possibilities, with the last step being the one that resolves the first possibility.
        # (1) Choose the permutation hitting the greatest number of genes. (2) If not resolved,
        # choose the permutation with the fewest permuted positions. (3) If not resolved, break the
        # tie by choosing the first permutation in the table, which will favor permutations toward
        # the 5' end.
        are_permutations_unresolved = True
        if hits_df.groupby('seed_contig_name').ngroups == hits_df.groupby(
            ['seed_contig_name', 'seed_permutation']).ngroups:
            are_permutations_unresolved = False
        if are_permutations_unresolved: # (1)
            hits_df['count'] = hits_df.groupby(
                ['seed_contig_name', 'seed_permutation'], as_index=False)[
                    'seed_contig_name'].transform(len)
            hits_df = hits_df[
                hits_df['count'] == hits_df.groupby('seed_contig_name')['count'].transform('max')]
            hits_df = hits_df.drop('count', axis=1)
            if hits_df.groupby('seed_contig_name').ngroups == hits_df.groupby(
                ['seed_contig_name', 'seed_permutation']).ngroups:
                are_permutations_unresolved = False
        if are_permutations_unresolved: # (2)
            hits_df['num_permuted_positions'] = hits_df[
                'seed_permutation'].apply(lambda p: p.count('_'))
            hits_df = hits_df[
                hits_df['num_permuted_positions'] == hits_df.groupby('seed_contig_name')[
                    'num_permuted_positions'].transform('min')]
            hits_df = hits_df.drop('num_permuted_positions', axis=1)
            if hits_df.groupby('seed_contig_name').ngroups == hits_df.groupby(
                ['seed_contig_name', 'seed_permutation']).ngroups:
                are_permutations_unresolved = False
        if are_permutations_unresolved: # (3)
            hits_df = hits_df[hits_df['seed_permutation'] == hits_df.groupby('seed_contig_name')[
                'seed_permutation'].transform('first')]

        ##################################################
        # Issues can arise in the selection of accurate seeds matching genes, especially in
        # relatively deeply sequenced samples with high coverages, such as tRNA-seq libraries of
        # pure cultures.
        # I. Seeds can be artifacts of the anvi'o de novo tRNA-seq workflow. `anvi-merge-trnaseq`
        # reports up to the number of seeds set by the user. If the user asks for 1,000 seeds from a
        # bacterial isolate experiment, then ~25-50 of these seeds will be true tRNA sequences and
        # up to ~950-975 will be artifacts (containing unaccounted modification-induced indels,
        # nontemplated nucleotides, sequence errors, etc., typically at low frequency), that could
        # not be resolved as non-tRNA by the tRNA-seq workflow. To remove these artifact seeds, hits
        # to the same gene are sorted by number of mismatches in the alignment and seed abundance,
        # and only the lowest mismatch/highest seed abundance hit is retained. Seed abundance is
        # taken as the average of relative abundance in each sample based on 3' (discriminator
        # nucleotide) coverage of the seed. For example, if there are two tRNA-seq samples in the
        # experiment, and two seeds hit the same gene each with one mismatch, but one seed has
        # relative 3' abundances of 0.02 and 0.03 in the two samples and the other seed has
        # abundances of 0.0006 and 0.00008, then the hit to the former seed will be the only one
        # retained for this gene.
        # II. The selection of the lowest mismatch seed (see the previous section) can create an
        # unintended side effect. The A -> I34 wobble position modification is typically nearly 100%
        # complete. I is detected as G in tRNA-seq reads, so the correct seed matching the gene
        # should have at least one alignment mismatch, G/A at position 34. In very deeply sequenced
        # samples, however, a seed with A34 can sometimes be detected from rare tRNA molecules
        # lacking the modification. `anvi-merge-trnaseq` would not merge the A34 and G34 seeds due
        # to the absence of a third or fourth mutated nucleotide at position 34 and the miniscule
        # frequency of A. Therefore, in this case, the lowest mismatch seed should not be selected;
        # rather, select the lowest mismatch seed with G34. The algorithm also confirms that the G34
        # seed is >10x more abundant than the A34 seed.
        coverage_df = pd.read_csv(self.seeds_specific_txt_path, sep='\t', header=0, skiprows=[1, 2])
        coverage_df = coverage_df[['contig_name', 'sample_name', 'relative_discriminator_coverage']]
        coverage_df = coverage_df.rename({'contig_name': 'seed_contig_name'}, axis=1)
        coverage_df = coverage_df[
            coverage_df['seed_contig_name'].isin(hits_df['seed_contig_name'].unique())]
        coverage_df = hits_df.merge(coverage_df, on='seed_contig_name')

        # Isolate the nucleotide at wobble position 34 in seeds that hit tRNAs with A34.
        trnaseq_contigs_db = self.trnaseq_contigs_db_info.load_db()
        # Convert seed contigs names to gene callers IDs.
        seed_contig_names_string = ','.join(
            ['"%s"' % seed_contig_name for seed_contig_name in
             hits_df[hits_df['anticodon'].str[0] == 'A']['seed_contig_name'].unique()])
        contigs_where_clause = f'''contig IN ({seed_contig_names_string})'''
        seed_id_df = trnaseq_contigs_db.get_table_as_dataframe(
            'genes_in_contigs',
            columns_of_interest=['gene_callers_id', 'contig'],
            where_clause=contigs_where_clause)
        seed_id_df = seed_id_df.rename(
            {'gene_callers_id': 'seed_gene_callers_id', 'contig': 'seed_contig_name'}, axis=1)
        # Find the index of position 34 in each of the seed sequences.
        seed_ids_string = ','.join(['"%s"' % seed_gene_callers_id for seed_gene_callers_id in
                                    seed_id_df['seed_gene_callers_id'].unique()])
        ids_where_clause = f'''gene_callers_id IN ({seed_ids_string})'''
        seed_wobble_df = trnaseq_contigs_db.get_table_as_dataframe(
            'trna_feature',
            columns_of_interest=['gene_callers_id', 'anticodon_loop_start'],
            where_clause=ids_where_clause)
        seed_wobble_df = seed_wobble_df.rename(
            {'gene_callers_id': 'seed_gene_callers_id',
             'anticodon_loop_start': 'seed_anticodon_loop_start'}, axis=1)
        seed_wobble_df['seed_anticodon_start'] = seed_wobble_df['seed_anticodon_loop_start'] + 2
        seed_wobble_df = seed_wobble_df.drop('seed_anticodon_loop_start', axis=1)
        seed_wobble_df = seed_id_df.merge(seed_wobble_df, on='seed_gene_callers_id')
        # Get the seed consensus sequence strings.
        seed_consensus_sequence_df = trnaseq_contigs_db.get_table_as_dataframe(
            'contig_sequences', where_clause=contigs_where_clause)
        trnaseq_contigs_db.disconnect()
        seed_consensus_sequence_df = seed_consensus_sequence_df.rename(
            {'contig': 'seed_contig_name', 'sequence': 'seed_sequence'}, axis=1)
        seed_wobble_df = seed_wobble_df.merge(seed_consensus_sequence_df, on='seed_contig_name')
        # Find the nucleotides read at wobble position 34 in the seeds.
        anticodon_wobble_nucleotides = []
        for anticodon_start, seed_consensus_sequence in zip(
            seed_wobble_df['seed_anticodon_start'], seed_wobble_df['seed_sequence']):
            anticodon_wobble_nucleotides.append(seed_consensus_sequence[anticodon_start])
        seed_wobble_df['seed_anticodon_wobble_nucleotide'] = anticodon_wobble_nucleotides

        def filter_multiple_hits_to_gene(gene_df): # inner function used in groupby apply
            min_mismatch_df = gene_df[gene_df['mismatch'] == gene_df['mismatch'].min()]
            if min_mismatch_df['seed_contig_name'].nunique() > 1:
                min_mismatch_df = min_mismatch_df[
                    min_mismatch_df['seed_contig_name'] == min_mismatch_df.groupby(
                        'seed_contig_name')['relative_discriminator_coverage'].mean().sort_values(
                            ascending=False).index[0]]
            if gene_df['anticodon'].iloc[0][0] != 'A':
                return min_mismatch_df
            else:
                # Case II: Address possibility that seed with I34 is neglected.
                max_coverage_seed_contig_name = gene_df.groupby('seed_contig_name')[
                        'relative_discriminator_coverage'].mean().sort_values(
                            ascending=False).index[0]
                min_mismatch_seed_contig_name = min_mismatch_df['seed_contig_name'].iloc[0]
                if min_mismatch_seed_contig_name == max_coverage_seed_contig_name:
                    # The seed with the fewest mismatches also has the highest average discriminator
                    # coverage.
                    return min_mismatch_df
                wobble_df = gene_df.merge(seed_wobble_df, how='left', on='seed_contig_name')
                min_mismatch_seed_wobble_nucleotide = wobble_df[
                    wobble_df['seed_contig_name'] == min_mismatch_seed_contig_name][
                        'anticodon'].iloc[0][0]
                if min_mismatch_seed_wobble_nucleotide != 'A':
                    # The seed with the fewest mismatches (and which does not have the highest
                    # average discriminator coverage) does not have an A at position 34. A seed is
                    # not matched to the gene.
                    return pd.DataFrame().reindex_like(min_mismatch_df)
                wobble_df = wobble_df[wobble_df['seed_anticodon_wobble_nucleotide'] == 'G']
                wobble_df = wobble_df[wobble_df['mismatch'] == wobble_df['mismatch'].min()]
                max_coverage_G34_seed_contig_name = wobble_df.groupby('seed_contig_name')[
                    'relative_discriminator_coverage'].mean().sort_values(ascending=False).index[0]
                if (wobble_df[wobble_df['seed_contig_name'] == max_coverage_G34_seed_contig_name][
                    'relative_discriminator_coverage'].mean() >= 10 * min_mismatch_df[
                        'relative_discriminator_coverage'].mean()):
                    # The seed with the fewest mismatches and a G at position 34 has an average
                    # discriminator coverage at least an order of magnitude higher than the the seed
                    # with the fewest mismatches and an A at position 34. Match the former seed to
                    # the gene instead of the latter.
                    return gene_df[gene_df['seed_contig_name'] == max_coverage_G34_seed_contig_name]
                else:
                    # The seed with the fewest mismatches and a G at position 34 does not have an
                    # average discriminator coverage at least an order of magnitude higher than the
                    # the seed with the fewest mismatches and an A at position 34. A seed is not
                    # matched to the gene.
                    return pd.DataFrame().reindex_like(min_mismatch_df)

        coverage_df = coverage_df.groupby(
            'gene_callers_id', group_keys=False).apply(filter_multiple_hits_to_gene)
        hits_df = hits_df[
            hits_df['seed_contig_name'].isin(coverage_df['seed_contig_name'].unique())]
        ##################################################

        # Add seed gene callers IDs to the table. (Both seed "contig" names and gene callers IDs are
        # unique.)
        seed_id_df = self.trnaseq_contigs_db_info.load_db().get_table_as_dataframe(
            'genes_in_contigs', columns_of_interest=['gene_callers_id', 'contig'])
        seed_id_df = seed_id_df.rename(
            {'gene_callers_id': 'seed_gene_callers_id', 'contig': 'seed_contig_name'})
        hits_df = hits_df.merge(seed_id_df, how='left', on='seed_contig_name')

        # Polish the columns. Order them how they will appear in the hits table in the database.
        hits_df['seed_alignment_start'] = hits_df['qstart'] - 1
        hits_df['gene_alignment_start'] = hits_df['sstart'] - 1
        hits_df = hits_df.drop(['qstart', 'qlen', 'sseqid', 'sstart'], axis=1)
        hits_df = hits_df.rename(
            {'gene_callers_id': 'gene_gene_callers_id', 'send': 'gene_alignment_stop'}, axis=1)
        hits_df = hits_df[['seed_gene_callers_id', # seed info
                           'seed_contig_name',
                           'seed_permutation',
                           'contigs_db_project_name', # gene and genome info
                           'contigs_db_hash',
                           'gene_contig_name',
                           'profile_db_sample_id',
                           'collection_name',
                           'bin_id',
                           'gene_gene_callers_id',
                           'decoded_amino_acid',
                           'anticodon',
                           'gene_start_in_contig',
                           'gene_stop_in_contig',
                           'trnascan_score',
                           'gene_sequence',
                           'mismatch', # hit info
                           'bitscore',
                           'seed_alignment_start',
                           'gene_alignment_start',
                           'gene_alignment_stop']]

        return hits_df


    def find_unmodified_nucleotides(self, hits_df):
        """
        Find the unmodified nucleotides at predicted modification positions in tRNA-seq seeds
        using matching tRNA gene sequences.

        Parameters
        ==========
        hits_df : pandas.core.frame.DataFrame
            Each row contains a selected hit between a seed and tRNA gene.

        Returns
        =======
        unmodified_nt_df : pandas.core.frame.DataFrame
            Each row contains a modification for which the underlying nucleotide could be resolved.
        """
        # Load modification information for seeds associated with genes.
        modifications_df = pd.read_csv(self.modifications_txt_path, sep='\t', header=0,
                                       usecols=['contig_name', 'seed_position'])
        modifications_df = modifications_df.rename({'contig_name': 'seed_contig_name'}, axis=1)
        modifications_df = modifications_df.drop_duplicates()
        modifications_df = modifications_df[
            modifications_df['seed_contig_name'].isin(hits_df['seed_contig_name'].unique())]

        # If a seed matches multiple genes and the nucleotides at a predicted modification position
        # differ between the genes, then it is likely that the variation is genetic rather than
        # caused by a modification.
        modification_candidates_df = modifications_df.merge(
            hits_df[['seed_contig_name', 'seed_alignment_start', 'gene_sequence']],
            how='left', on='seed_contig_name')
        modification_keys = []
        unmodified_nts = []
        variant_keys = []
        for group_key, modification_candidate_df in modification_candidates_df.groupby(
            ['seed_contig_name', 'seed_position'], as_index=False):
            unmodified_nt = ''
            for seed_position, seed_alignment_start, gene_sequence in zip(
                modification_candidate_df['seed_position'],
                modification_candidate_df['seed_alignment_start'],
                modification_candidate_df['gene_sequence']):
                gene_nt = gene_sequence[int(seed_position - seed_alignment_start)]
                if unmodified_nt:
                    if gene_nt != unmodified_nt:
                        variant_keys.append(group_key)
                        break
                else:
                    unmodified_nt = gene_nt
            else:
                modification_keys.append(group_key)
                unmodified_nts.append(unmodified_nt)
        modifications_df = modifications_df.set_index(['seed_contig_name', 'seed_position'])
        modifications_df = modifications_df.loc[modification_keys]
        unmodified_nt_df = modifications_df.merge(
            pd.DataFrame(
                [modification_key + (unmodified_nt, )
                 for modification_key, unmodified_nt in zip(modification_keys, unmodified_nts)],
                columns=['seed_contig_name', 'seed_position', 'unmodified_nt']),
            on=['seed_contig_name', 'seed_position'])
        unmodified_nt_df = unmodified_nt_df.reset_index(drop=True)

        return unmodified_nt_df


    def update_trnaseq_contigs_database(self, hits_df, unmodified_nt_df):
        """
        Add information on tRNA gene associations to the tRNA-seq contigs database.

        Parameters
        ==========
        hits_df : pandas.core.frame.DataFrame
            Each row contains a selected hit between a seed and tRNA gene.

        unmodified_nt_df : pandas.core.frame.DataFrame
            Each row contains a modification for which the underlying nucleotide could be resolved.
        """
        trnaseq_contigs_db = self.trnaseq_contigs_db_info.load_db()

        # Either clear the table of seed/gene matches with `self.remove_previous_matches` or append
        # to the table.
        row_count = trnaseq_contigs_db.get_row_counts_from_table(tables.trna_gene_hits_table_name)
        if self.remove_previous_matches:
            trnaseq_contigs_db._exec(f'''DELETE FROM {tables.trna_gene_hits_table_name}''')
            self.run.info_single(
                f"{pp(row_count)} seed/gene matches dropped from the tRNA-seq contigs database",
                cut_after=0)
        else:
            if row_count:
                self.run.info_single(
                    f"Appending to the {pp(row_count)} seed/gene matches previously stored in the "
                    "tRNA-seq contigs database",
                    cut_after=0)

        # Add the unmodified nucleotides to the rows of the table.
        table_entries = []
        hit_id = 0
        for row in hits_df.itertuples(index=False):
            seed_unmodified_nt_df = unmodified_nt_df[
                unmodified_nt_df['seed_contig_name'] == row.seed_contig_name]

            if len(seed_unmodified_nt_df):
                # Note that modification positions at which the unmodified nucleotide could not be
                # resolved will not be represented in the entry.
                seed_unmodified_nt_series = (seed_unmodified_nt_df['seed_position'].astype(str) +
                                             seed_unmodified_nt_df['unmodified_nt'])
                unmodified_nt_entry = ','.join(seed_unmodified_nt_series.tolist())
            else:
                unmodified_nt_entry = ''

            table_entries.append([hit_id,
                                  row.seed_gene_callers_id, # seed info
                                  row.seed_contig_name,
                                  row.contigs_db_project_name, # gene and genome info
                                  row.contigs_db_hash,
                                  row.gene_contig_name,
                                  row.profile_db_sample_id,
                                  row.collection_name,
                                  row.bin_id,
                                  row.gene_gene_callers_id,
                                  row.decoded_amino_acid,
                                  row.anticodon,
                                  row.gene_start_in_contig,
                                  row.gene_stop_in_contig,
                                  row.trnascan_score,
                                  row.gene_sequence,
                                  row.mismatch, # hit info
                                  row.bitscore,
                                  row.seed_alignment_start,
                                  row.gene_alignment_start,
                                  row.gene_alignment_stop,
                                  unmodified_nt_entry])
            hit_id += 1
        trnaseq_contigs_db._exec_many(
            f'''INSERT INTO {tables.trna_gene_hits_table_name} VALUES '''
            f'''({",".join(["?"] * len(tables.trna_gene_hits_table_structure))})''', table_entries)
        trnaseq_contigs_db.disconnect()

        self.run.info_single(
            f"{hits_df['seed_gene_callers_id'].nunique()} tRNA-seq seeds are found to match "
            f"{hits_df['gene_gene_callers_id'].nunique()} tRNA genes",
            cut_after=0)


    @staticmethod
    def get_integrated_genomes(trnaseq_contigs_db_path):
        """
        Get the project names and hashes of (meta)genomic contigs databases and any bins in which
        tRNA genes linked to the 'trnaseq'-variant contigs database were found.

        Parameters
        ==========
        trnaseq_contigs_db_path : str
            Path to 'trnaseq'-variant contigs database.

        Returns
        =======
        integrated_genome_dict : dict
            Nested dictionary with levels for contigs database, profile database, collection, and
            bins.
        """
        trnaseq_contigs_db_info = DBInfo(trnaseq_contigs_db_path, expecting='contigs')

        if trnaseq_contigs_db_info.variant != 'trnaseq':
            raise ConfigError(
                f"The database at '{trnaseq_contigs_db_path}' was a "
                f"'{trnaseq_contigs_db_info.variant}' variant, not the required 'trnaseq' variant.")

        trnaseq_contigs_db = trnaseq_contigs_db_info.load_db()
        trna_gene_hits_df = trnaseq_contigs_db.get_table_as_dataframe(
            tables.trna_gene_hits_table_name,
            columns_of_interest=['gene_contigs_db_project_name',
                                 'gene_contigs_db_hash',
                                 'profile_db_sample_id',
                                 'collection_name',
                                 'bin_id'])
        integrated_genome_dict = {}
        for row in trna_gene_hits_df.itertuples(index=False):
            if not row.contigs_db_project_name or not row.contigs_db_hash:
                raise ConfigError(
                    "For some reason a row of the tRNA gene hits table in the tRNA-seq contigs "
                    f"database at '{trnaseq_contigs_db_path}' does not have a proper (meta)genomic "
                    "contigs database identifier indicating the source of the gene. The contigs "
                    "database should be identified by both a project name and a hash. Here is all "
                    "of the information from the erroneous row. Contigs database project name: "
                    f"{row.contigs_db_project_name}   Contigs database hash: {row.contigs_db_hash} "
                    f"  Profile database sample ID: {row.profile_db_sample_id}   Collection name: "
                    f"{row.collection_name}   Bin ID: {row.bin_id}")

            contigs_db_key = (row.contigs_db_project_name, row.contigs_db_hash)
            try:
                profile_db_dict = integrated_genome_dict[contigs_db_key]
            except KeyError:
                integrated_genome_dict[contigs_db_key] = profile_db_dict = {}

            if not row.profile_db_sample_id and not row.collections_name and not row.bin_id:
                continue
            elif row.profile_db_sample_id and row.collections_name and row.bin_id:
                pass
            else:
                raise ConfigError(
                    "For some reason a row of tRNA gene hits only contains some but not all of the "
                    "information needed to identify a bin. A profile database sample ID, "
                    "collection name, and bin ID should all be provided. Here is all of the "
                    "information from the erroneous row. Contigs database project name: "
                    f"{row.contigs_db_project_name}   Contigs database hash: {row.contigs_db_hash} "
                    f"  Profile database sample ID: {row.profile_db_sample_id}   Collection name: "
                    f"{row.collection_name}   Bin ID: {row.bin_id}")

            try:
                collections_dict = profile_db_dict[row.profile_db_sample_id]
            except KeyError:
                profile_db_dict[row.profile_db_sample_id] = collections_dict = {}

            try:
                bin_ids = collections_dict[row.collection_name]
            except KeyError:
                collections_dict[row.collection_name] = bin_ids = set()
            bin_ids.add(row.bin_id)

        return integrated_genome_dict


class Affinitizer:
    """Using the `go` method, relates changes in tRNA-seq seed abundances to the codon usage of gene
    functions."""
    default_min_coverage = 10
    default_min_isoacceptors = 4
    default_rarefaction_limit = 0

    def __init__(self, args={}, p=progress, r=run, do_sanity_check=True):
        self.progress = p
        self.run = r

        self.args = args
        A = lambda x: args.__dict__[x] if x in args.__dict__ else None
        self.trnaseq_contigs_db_path = A('trnaseq_contigs_db')
        self.seeds_specific_txt_path = A('seeds_specific_txt')
        self.genomic_contigs_db_path = A('contigs_db')
        self.codon_frequencies_path = A('codon_frequencies_txt')
        self.reference_sample_name = A('reference_sample')
        self.nonreference_sample_names = A('sample_subset')
        self.min_coverage = A('min_coverage')
        if self.min_coverage == None:
            self.min_coverage = self.default_min_coverage
        self.min_isoacceptors = A('min_isoacceptors')
        if self.min_isoacceptors == None:
            self.min_isoacceptors = self.default_min_isoacceptors
        self.rarefaction_limit = A('rarefaction_limit')
        if self.rarefaction_limit == None:
            self.rarefaction_limit = self.default_rarefaction_limit

        if do_sanity_check:
            self.sanity_check()

        self.trnaseq_contigs_db_info = DBInfo(self.trnaseq_contigs_db_path)
        self.genomic_contigs_db_info = DBInfo(self.genomic_contigs_db_path)

        # Find the names of the samples to analyze in addition to the reference sample: the
        # "nonreference" samples. By default, this will be every other sample, but a subset of
        # available samples can also be used.
        if self.nonreference_sample_names == None:
            self.nonreference_sample_names = pd.read_csv(self.seeds_specific_txt_path, sep='\t', header=0, skiprows=[1, 2], usecols=['sample_name'])['sample_name'].unique().tolist()
            self.nonreference_sample_names.remove(self.reference_sample_name)
        else:
            self.nonreference_sample_names = self.nonreference_sample_names.split(',')
        self.sample_names = [self.reference_sample_name] + self.nonreference_sample_names


    def sanity_check(self):
        """Check the feasibility of args from initialization."""

        trnaseq_contigs_db_info = DBInfo(self.trnaseq_contigs_db_path, expecting='contigs')
        if trnaseq_contigs_db_info.variant != 'trnaseq':
            raise ConfigError(f"The database at '{self.trnaseq_contigs_db_path}' was a '{trnaseq_contigs_db_info.variant}' variant, "
                              "not the required 'trnaseq' variant.")
        with trnaseq_contigs_db_info.load_db() as trnaseq_contigs_db:
            if len(trnaseq_contigs_db.get_table_as_dataframe('trna_gene_hits', columns_of_interest=['seed_contig_name'])) == 0:
                raise ConfigError(f"It appears that no tRNA seeds in the tRNA-seq contigs database, '{self.trnaseq_contigs_db_path}', "
                                  f"are linked to tRNA genes in the (meta)genomic contigs database, '{self.genomic_contigs_db_path}'.")

        filesnpaths.is_file_exists(self.seeds_specific_txt_path)

        genomic_contigs_db_info = DBInfo(self.genomic_contigs_db_path, expecting='contigs')
        if genomic_contigs_db_info.variant != 'unknown':
            raise ConfigError(f"The database at '{self.genomic_contigs_db_path}' was a '{genomic_contigs_db_info.variant}' variant. "
                              "This should be a normal (meta)genomic contigs database, technically an 'unknown' variant, produced by `anvi-gen-contigs-database`.")
        if 'modules_db_hash' not in genomic_contigs_db_info.get_self_table():
            raise ConfigError(f"It appears that genes have not been annotated by KOfams in the (meta)genomic contigs database, '{self.genomic_contigs_db_path}'. "
                              "Please run `anvi-run-kegg-kofams` on the database and try again.")
        if 'KEGG_BRITE' not in genomic_contigs_db_info.get_self_table()['gene_function_sources'].split(','):
            raise ConfigError("The version of the Modules database that was used in `anvi-run-kegg-kofams` does not include KEGG BRITE annotations. "
                              "The calculation of functional affinity makes use of the functional classification of gene orthologs in BRITE hierarchies. "
                              "Please update the Modules database by rerunning `anvi-setup-kegg-kofams`, "
                              f"and then rerun `anvi-run-kegg-kofams` on the (meta)genomics contigs database, '{self.genomic_contigs_db_path}'.")

        filesnpaths.is_file_exists(self.codon_frequencies_txt_path)

        available_sample_names = pd.read_csv(self.seeds_specific_txt_path, sep='\t', header=0, skiprows=[1, 2], usecols=['sample_name'])['sample_name'].unique().tolist()
        # Check for the existence of the reference sample.
        if self.reference_sample_name not in available_sample_names:
            raise ConfigError(f"The desired reference sample name, '{self.reference_sample_name}', "
                              f"was not found in `seeds-specific-txt`, '{self.seeds_specific_txt_path}'. "
                              f"Here are the samples provided in that table: {', '.join(available_sample_names)}")
        # Check for the existence of a given subset of sample names.
        if self.nonreference_sample_names:
            nonreference_sample_names = self.nonreference_sample_names.split(',')
            bad_sample_names = set(nonreference_sample_names).difference(set(available_sample_names))
            if bad_sample_names:
                raise ConfigError("The following desired subset sample names were not found in `seeds-specific-txt`, "
                                  f"'{self.seeds_specific_txt_path}': {', '.join(bad_sample_names)}. "
                                  f"Here are the samples provided in that table: {', '.join(available_sample_names)}")
            if self.reference_sample_name in nonreference_sample_names:
                raise ConfigError(f"Please do not include the reference sample, '{self.reference_sample_name}' "
                                  f"in the subset of sample names: {', '.join(nonreference_sample_names)}. Sorry for the sclerotic idiocy.")
        else:
            nonreference_sample_names = available_sample_names
            nonreference_sample_names.remove(self.reference_sample_name)
        if len(nonreference_sample_names) == 0:
            raise ConfigError(f"There must be one or more samples beside the reference sample in `seeds-specific-txt`, '{self.seeds_specific_txt}'. "
                              f"Only the reference sample, '{self.reference_sample_name}', was found.")

        if self.min_coverage < 1:
            raise ConfigError("The minimum coverage for a tRNA isoacceptor to be detected must be an integer "
                              f"greater than or equal to 1, not the provided value of {self.min_coverage}.")

        if self.min_isoacceptors < 1:
            raise ConfigError("The minimum number of tRNA isoacceptors for translational affinity to be calculated "
                              f"must be an integer greater or equal to 1, not the provided value of {self.min_isoacceptors}.")

        if self.rarefaction_limit < 0:
            raise ConfigError("The rarefaction limit on subsampled tRNA isoacceptors must be an integer "
                              f"greater than or equal to 1, not the provided value of {self.rarefaction_limit}.")


    def go(self):
        """Relate changes in tRNA-seq seed abundances to the codon usage of gene functions."""

        isoacceptors_df = self.load_isoacceptor_data()
        if len(isoacceptors_df) == 0:
            return
        isoacceptor_abundance_dict = self.get_isoacceptor_abundances(isoacceptors_df)
        kegg_df = self.consolidate_kegg_annotations()


    def load_isoacceptor_data(self):
        """Workhorse method to load and filter data, find properties of seeds, and ultimately group isoacceptors."""

        # Load data from the tRNA-seq contigs database.
        with self.trnaseq_contigs_db_info.load_db() as trnaseq_contigs_db:
            trna_gene_hits_df = trnaseq_contigs_db.get_table_as_dataframe(
                'trna_gene_hits', columns_of_interest=['seed_contig_name', 'bin_name', 'decoded_amino_acid', 'anticodon', 'gene_sequence'])
            # Drop duplicate rows (only preserve one row) representing hits between the same
            # tRNA-seq seed and different tRNA genes with identical sequences.
            trna_gene_hits_df = trna_gene_hits_df.drop_duplicates()
            if self.genomic_collection_name == None:
                trna_gene_hits_df['bin_name'] = ''

            self.run.info("tRNA-seq seeds linked to tRNA genes", len(trna_gene_hits_df))

            # Get the gene callers IDs of the tRNA-seq seeds.
            seed_contig_names = ','.join(['"%s"' % seed_contig_name for seed_contig_name in trna_gene_hits_df['seed_contig_name'].unique()])
            contigs_where_clause = f'''contig IN ({seed_contig_names})'''
            seed_id_df = trnaseq_contigs_db.get_table_as_dataframe(
                'genes_in_contigs', columns_of_interest=['gene_callers_id', 'contig'], where_clause=contigs_where_clause)
            seed_id_df = seed_id_df.rename({'contig': 'seed_contig_name', 'gene_callers_id': 'seed_gene_callers_id'}, axis=1)
            trna_gene_hits_df = trna_gene_hits_df.merge(seed_id_df, on='seed_contig_name')

            # Find wobble position 34 in the tRNA-seq seed sequences.
            seed_gene_callers_ids = ','.join(['"%s"' % gene_callers_id for gene_callers_id in trna_gene_hits_df['seed_gene_callers_id'].unique()])
            ids_where_clause = f'''gene_callers_id IN ({seed_gene_callers_ids})'''
            wobble_position_df = trnaseq_contigs_db.get_table_as_dataframe(
                'trna_feature', columns_of_interest=['gene_callers_id', 'anticodon_loop_start'], where_clause=ids_where_clause)
            wobble_position_df = wobble_position_df.rename({'gene_callers_id': 'seed_gene_callers_id'}, axis=1)
            wobble_position_df['anticodon_start'] = wobble_position_df['anticodon_loop_start'] + 2
            wobble_position_df = wobble_position_df.drop('anticodon_loop_start', axis=1)
            trna_gene_hits_df = trna_gene_hits_df.merge(wobble_position_df, on='seed_gene_callers_id')

            # Get the tRNA-seq seed consensus sequence strings.
            seed_consensus_sequence_df = trnaseq_contigs_db.get_table_as_dataframe('contig_sequences', where_clause=contigs_where_clause)
            seed_consensus_sequence_df = seed_consensus_sequence_df.rename({'contig': 'seed_contig_name', 'sequence': 'seed_sequence'}, axis=1)
            trna_gene_hits_df = trna_gene_hits_df.merge(seed_consensus_sequence_df, on='seed_contig_name')

            # Find the nucleotides read at wobble position 34 in the tRNA-seq seeds.
            anticodon_wobble_nucleotides = []
            for anticodon_start, seed_consensus_sequence in zip(trna_gene_hits_df['anticodon_start'], trna_gene_hits_df['seed_sequence']):
                anticodon_wobble_nucleotides.append(seed_consensus_sequence[anticodon_start])
            trna_gene_hits_df['seed_anticodon_wobble_nucleotide'] = anticodon_wobble_nucleotides

            trna_gene_hits_df = trna_gene_hits_df.drop(['seed_gene_callers_id', 'anticodon_start', 'seed_sequence'], axis=1)

        # Load data from the seeds specific coverage table.
        coverage_df = pd.read_csv(self.seeds_specific_txt_path, sep='\t', header=0, skiprows=[1, 2],
                                  usecols=['contig_name', 'sample_name', 'relative_discriminator_coverage', 'discriminator_1'])
        coverage_df = coverage_df.rename({'contig_name': 'seed_contig_name'}, axis=1)
        # Select data for the samples of interest.
        coverage_df = coverage_df[coverage_df['sample_name'].isin(self.sample_names)]
        # Select data for the tRNA-seq seeds linked to tRNA genes.
        coverage_df = coverage_df[coverage_df['seed_contig_name'].isin(trna_gene_hits_df['seed_contig_name'].unique())]
        coverage_df = coverage_df[coverage_df['discriminator_1'] >= self.min_coverage]
        coverage_df = coverage_df.drop('discriminator_1', axis=1)

        # Ignore tRNA-seq seeds that do not have coverage in the reference sample.
        coverage_df = coverage_df.groupby('seed_contig_name').filter(
            lambda seed_coverage_df: self.reference_sample_name in seed_coverage_df['sample_name'].values)

        self.run.info("Filtered seeds detected in reference sample", len(coverage_df['seed_contig_name'].unique()))

        # Ignore seeds that are in only one sample.
        coverage_df = coverage_df.groupby('seed_contig_name').filter(lambda seed_coverage_df: len(seed_coverage_df) > 1)

        self.run.info("Filtered seeds detected in >1 sample", len(coverage_df['seed_contig_name'].unique()))

        if len(coverage_df) == 0:
            self.info.warning(f"No seeds remain after applying the seed detection coverage threshold of {self.min_coverage}. "
                              "This threshold must be met in both the reference sample and another sample.")

        # Evaluate the anticodon wobble nucleotide in the seed.
        effective_wobble_nucleotides = []
        for decoded_aa_type, anticodon, seed_wobble_nucleotide in zip(
            trna_gene_hits_df['decoded_amino_acid'], trna_gene_hits_df['anticodon'], trna_gene_hits_df['seed_anticodon_wobble_nucleotide']):
            if decoded_aa_type == 'Ile2':
                # tRNA-Ile2 has a wobble nucleotide of lysidine in bacteria or agmatidine in
                # archaea, which are given the same decoding weight.
                effective_wobble_nucleotides.append('L')
                continue
            elif anticodon[0] == 'A':
                # Check for modification of A34 to I, which is detected as G in tRNA-seq reads.
                # tRNA-Arg-ACG and tRNA-Leu-AAG are the only bacterial tRNAs known to contain I34.
                # No archaeal tRNAs are known to contain I34. I34 has been found in 8 eukaryotic
                # tRNAs. As far as I know, the I modification is pervasive at position 34 in the
                # tRNAs that have it, so presence of G34 in the seed consensus sequence is assumed
                # to be 100% modification.
                if seed_wobble_nucleotide == 'G':
                    effective_wobble_nucleotides.append('I')
                    continue
            effective_wobble_nucleotides.append(anticodon[0])

        trna_gene_hits_df['effective_wobble_nucleotide'] = effective_wobble_nucleotides
        trna_gene_hits_df = trna_gene_hits_df.drop(['gene_sequence', 'seed_anticodon_wobble_nucleotide'], axis=1)

        # Drop duplicate rows (preserve a single row) representing hits between the same tRNA-seq
        # seed and tRNA genes with different sequences. This should only occur if the seed is a
        # partial read of the tRNA, and the genes differ beyond the 5' end of the seed. However,
        # confirm that the hits yielded the same anticodon wobble nucleotide, just in case I'm
        # missing something.
        trna_gene_hits_df = trna_gene_hits_df.drop_duplicates()
        if trna_gene_hits_df.groupby('seed_contig_name').ngroups != trna_gene_hits_df.groupby(
            ['seed_contig_name', 'effective_wobble_nucleotide']).ngroups:
            confusing_df = trna_gene_hits_df.groupby('seed_contig_name').filter(lambda seed_df: len(seed_df) > 1)
            raise ConfigError("A strange circumstance has occurred where a tRNA-seq seed linked to tRNA genes "
                              "with different sequences was found to have different effective wobble nucleotides. "
                              f"Here are the entries for the seeds in question:\n{confusing_df.to_string()}")

        seeds_df = trna_gene_hits_df.merge(coverage_df, how='inner', on='seed_contig_name')

        # Perhaps isoacceptors in the same bin could differ in their effective wobble nucleotide:
        # say one is modified to I and the other is kept A. This oddity should be noted.
        if seeds_df.groupby(['bin_name', 'decoded_amino_acid', 'anticodon']).ngroups != seeds_df.groupby(
            ['bin_name', 'decoded_amino_acid', 'anticodon', 'effective_wobble_nucleotide']).ngroups:
            confusing_df = seeds_df.groupby(['decoded_amino_acid', 'anticodon']).filter(
                lambda isoacceptor_df: len(isoacceptor_df['effective_wobble_nucleotide'].unique()) > 1)
            self.run.warning("A very strange circumstance has been found in which apparent tRNA-seq seed isoacceptors "
                             "from the same bin differ in their anticodon wobble nucleotide. "
                             "For example, in one seed, the nucleotide could be A while in the other it is modified to I. "
                             f"Here are the entries for the seeds in question: {confusing_df.to_string()}")
            seeds_df = seeds_df.groupby(['decoded_amino_acid', 'anticodon']).filter(
                lambda isoacceptor_df: len(isoacceptor_df['effective_wobble_nucleotide'].unique()) == 1)

        seeds_df = seeds_df.drop('seed_contig_name', axis=1)

        # Aggregate seeds representing isoacceptors in a genome, summing their coverages.
        isoacceptors_df = seeds_df.groupby(
            ['bin_name', 'decoded_amino_acid', 'anticodon', 'effective_wobble_nucleotide', 'sample_name'], as_index=False).agg('sum')

        # Remove bins lacking a diversity of isoacceptors.
        prefilter_bin_names = set(isoacceptors_df['bin_name'])
        isoacceptors_df = isoacceptors_df.groupby('bin_name').filter(
            lambda bin_df: bin_df.groupby(['decoded_amino_acid', 'anticodon']).ngroups >= self.min_isoacceptors)
        removed_bin_names = set(prefilter_bin_names).difference(set(isoacceptors_df['bin_name']))
        if removed_bin_names:
            self.info.warning(f"The following bins did not meet the bin isoacceptor threshold of {self.min_isoacceptors}: {', '.join(removed_bin_names)}")
        if len(isoacceptors_df) == 0:
            self.info.warning(f"No seeds remain after applying the bin isoacceptor threshold of {self.min_isoacceptors}.")

        return isoacceptors_df


    def get_isoacceptor_abundances(self, isoacceptors_df):
        """Get isoacceptor abundance data.

        The keys of the returned dictionary are bin names and the values are tuples of length 2.
        There is a value for each isoacceptor + nonreference sample, i.e., isoacceptor 1 + sample X,
        isoacceptor 1 + sample X, isoacceptor 2 + sample Y, etc.

        The tuple contains 1) a list and 2) a numpy array with items for each of the processed
        isoacceptor + nonreference sample rows. The list contains tuples of length 3: item 1)
        decoded amino acid, 2) anticodon, and 3) effective anticodon wobble position 34 nucleotide.
        Each item in the numpy array is the ratio of seed abundance in the nonreference versus
        reference sample. Abundance is based on coverage of the 3' discriminator nucleotide: the
        most accurate representation of abundance comes from the 3' end of the seed, as many reads
        can be 3' tRNA fragments resulting from truncation of reverse transcription. Example:
        Isoacceptor 1 has an abundance of 1% in sample X and an abundance of 0.5% in the reference
        sample; the ratio is 1% / 0.5% = 2.
        """

        isoacceptor_abundance_dict = {}
        for bin_name, bin_df in isoacceptors_df.groupby('bin_name'):
            reference_sample_df = bin_df[bin_df['sample_name'] == self.reference_sample_name]
            reference_sample_df = reference_sample_df.set_index(['decoded_amino_acid', 'anticodon', 'effective_wobble_nucleotide'])
            nonreference_isoacceptor_df = bin_df[bin_df['sample_name'] != self.reference_sample_name]

            decoding_keys = []
            isoacceptor_abundance_ratios = []
            for row in nonreference_isoacceptor_df.itertuples(index=False):
                decoding_key = (row.decoded_amino_acid, row.anticodon, row.effective_wobble_nucleotide)
                reference_abundance = reference_sample_df.loc[decoding_key]['relative_discriminator_coverage']
                decoding_keys.append(decoding_key)
                isoacceptor_abundance_ratios.append(row.relative_discriminator_coverage / reference_abundance)
            isoacceptor_abundance_dict[bin_name] = (decoding_keys, np.array(isoacceptor_abundance_ratios))

        return isoacceptor_abundance_dict


    def consolidate_kegg_annotations(self):
        with self.genomic_contigs_db_info.load_db() as genomic_contigs_db:
            kegg_df = genomic_contigs_db.get_table_as_dataframe('gene_functions', where_clause=f'''source IN ("KOfam", "KEGG_Module", "KEGG_Class")''')
        kofam_df = kegg_df[kegg_df['source'] == 'KOfam']
        module_df = kegg_df[kegg_df['source'] == 'KEGG_Module']
        class_df = kegg_df[kegg_df['source'] == 'KEGG_Class']

        # Iterate the annotations in each block of lines for KOfam, module, and class. Relate
        # module/class to KOfam annotations. There is the same number of module and class entries.
        # Not every KOfam is part of a module/class. There is only one edge case, presumably
        # vanishingly rare, that can prevent accurate reassignment of module/class to KOfam: a gene
        # is assigned multiple KOfams, the entries for the KOfams happen to be next to each other in
        # the table (which I think would happen randomly), and one or more of the KOfams is not part
        # of a module/class. This hypothetical edge case is resolved by assigning the module/class
        # to the first occurring KOfam.
        module_iter = iter(module_df.itertuples(index=False))
        class_iter = iter(class_df.itertuples(index=False))
        try:
            module_row = next(module_iter)
            class_row = next(class_iter)
            module_gene_callers_id = module_row.gene_callers_id
        except StopIteration:
            module_row = None
            class_row = None
            module_gene_callers_id = -1

        new_rows = []
        for kofam_row in kofam_df.itertuples(index=False):
            kofam_gene_callers_id = kofam_row.gene_callers_id
            if kofam_gene_callers_id == module_gene_callers_id:
                new_rows.append([
                    kofam_gene_callers_id,
                    kofam_row.accession,
                    kofam_row.function,
                    kofam_row.e_value,
                    module_row.accession,
                    module_row.function,
                    class_row.function
                ])

                try:
                    module_row = next(module_iter)
                    class_row = next(class_iter)
                    module_gene_callers_id = module_row.gene_callers_id
                except StopIteration:
                    module_row = None
                    class_row = None
                    module_gene_callers_id = -1
            else:
                new_rows.append([
                    kofam_gene_callers_id,
                    kofam_row.accession,
                    kofam_row.function,
                    kofam_row.e_value,
                    '',
                    '',
                    ''
                ])
        new_kegg_df = pd.DataFrame(new_rows, columns=['gene_callers_id',
                                                      'kofam_accession',
                                                      'kofam_function',
                                                      'kofam_e_value',
                                                      'kegg_module_accession',
                                                      'kegg_module_function',
                                                      'kegg_class_function'])
        return new_kegg_df


    @staticmethod
    def list_sample_names(args={}, r=run):
        """List samples in the tRNA-seq input files."""

        A = lambda x: args.__dict__[x] if x in args.__dict__ else None
        seeds_specific_txt_path = A('seeds_specific_txt')

        if seeds_specific_txt_path == None:
            raise ConfigError("To list samples in `seeds_specific_txt`, a path to this file must be provided.")
        filesnpaths.is_file_exists(seeds_specific_txt_path)

        available_sample_names = pd.read_csv(
            seeds_specific_txt_path, sep='\t', header=0, skiprows=[1, 2], usecols=['sample_name'])['sample_name'].unique().tolist()
        run.info_single(f"The `seeds_specific_txt` table, '{seeds_specific_txt_path}', "
                        f"contains the following samples: {', '.join(available_sample_names)}")

        return available_sample_names
