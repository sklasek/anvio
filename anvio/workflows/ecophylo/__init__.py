# -*- coding: utf-8
# pylint: disable=line-too-long
""" Classes to define and work with anvi'o ecophylo workflows. """

import os
import anvio
import pandas as pd

import anvio
import anvio.data.hmm
import anvio.utils as u
import anvio.terminal as terminal
import anvio.workflows as w
import anvio.filesnpaths as filesnpaths

from anvio.errors import ConfigError
from anvio.workflows import WorkflowSuperClass
from anvio.workflows.metagenomics import MetagenomicsWorkflow


__author__ = "Developers of anvi'o (see AUTHORS.txt)"
__copyright__ = "Copyleft 2015-2020, the Meren Lab (http://merenlab.org/)"
__credits__ = []
__license__ = "GPL 3.0"
__version__ = anvio.__version__
__maintainer__ = "Matthew S. Schechter"
__email__ = "mschechter@uchicago.edu"


run = terminal.Run()

class EcoPhyloWorkflow(WorkflowSuperClass):

    def __init__(self, args=None, run=terminal.Run(), progress=terminal.Progress()):
        self.init_workflow_super_class(args, workflow_name='ecophylo')

        # Snakemake rules
        self.rules.extend(['anvi_run_hmms_hmmsearch',
                           'filter_hmm_hits_by_query_coverage',
                           'anvi_get_sequences_for_hmm_hits',
                           'simplify_names_from_hmm_hits',
                           'cat_sequences_to_one_fasta',
                           'anvi_get_external_gene_calls_file',
                           'cat_external_gene_calls_file',
                           'cluster_X_percent_sim_mmseqs',
                           'subset_AA_seqs_with_mmseqs_reps',
                           'align_sequences',
                           'trim_alignment',
                           'remove_sequences_with_X_percent_gaps',
                           'count_num_sequences_filtered',
                           'subset_DNA_reps_with_QCd_AA_reps_for_mapping',
                           'subset_external_gene_calls_file_all',
                           'make_fasta_txt',
                           'fasttree',
                           'iqtree',
                           'make_metagenomics_config_file',
                           'run_metagenomics_workflow',
                           'add_default_collection',
                           'anvi_summarize',
                           'rename_tree_tips',
                           'make_anvio_state_file',
                           'make_misc_data',
                           'anvi_import_everything',
                           ])

        self.general_params.extend(['metagenomes']) # user needs to input a metagenomes.txt file
        self.general_params.extend(['external_genomes']) # user can add isolate genomes if needed
        self.general_params.extend(['hmm_list']) # user must input which Reference proteins will be used for workflow
        self.general_params.extend(['samples_txt']) # user must input which Reference proteins will be used for workflow


        # Parameters for each rule that are accessible in the config.json file
        rule_acceptable_params_dict = {}

        rule_acceptable_params_dict['filter_hmm_hits_by_query_coverage'] = ['--query-coverage', 'additional_params']
        rule_acceptable_params_dict['cluster_X_percent_sim_mmseqs'] = ['--min-seq-id']
        rule_acceptable_params_dict['trim_alignment'] = ['-gt', "-gappyout", 'additional_params']
        rule_acceptable_params_dict['remove_sequences_with_X_percent_gaps'] = ['--max-percentage-gaps']
        rule_acceptable_params_dict['fasttree'] = ['run']
        rule_acceptable_params_dict['iqtree'] = ['run', '-m', 'additional_params']
        rule_acceptable_params_dict['run_metagenomics_workflow'] = ['clusterize', 'cluster_submission_params']

        self.rule_acceptable_params_dict.update(rule_acceptable_params_dict)

        # Set default values for accessible rules and order of rules in config.json file
        self.default_config.update({
            'metagenomes': 'metagenomes.txt',
            'external_genomes': 'external-genomes.txt',
            'hmm_list': 'hmm_list.txt',
            'anvi_run_hmms_hmmsearch': {'threads': 5},
            'filter_hmm_hits_by_query_coverage': {'threads': 5, '--query-coverage': 0.8},
            'anvi_get_sequences_for_hmm_hits': {'threads': 2},
            'simplify_names_from_hmm_hits': {'threads': 2},
            'cat_sequences_to_one_fasta': {'threads': 2},
            'anvi_get_external_gene_calls_file': {'threads': 5},
            'cat_external_gene_calls_file': {'threads': 2},
            'cluster_X_percent_sim_mmseqs': {'threads': 5, '--min-seq-id': 0.94},
            'subset_AA_seqs_with_mmseqs_reps': {'threads': 2},
            'align_sequences': {'threads': 5},
            'trim_alignment': {'threads': 5, '-gappyout': True},
            'remove_sequences_with_X_percent_gaps': {'threads': 5, '--max-percentage-gaps': 50},
            'count_num_sequences_filtered': {'threads': 5},
            'subset_DNA_reps_with_QCd_AA_reps_for_mapping': {'threads': 2},
            'subset_external_gene_calls_file_all': {'threads': 2},
            'make_fasta_txt': {'threads': 5},
            'fasttree': {'run': True, 'threads': 5},
            'iqtree': {'threads': 5,'-m': "MFP"},
            'make_metagenomics_config_file': {'threads': 1},
            'run_metagenomics_workflow': {'threads': 5, 'clusterize': False},
            'add_default_collection': {'threads': 5},
            'anvi_summarize': {'threads': 5},
            'rename_tree_tips': {'threads': 1},
            'make_anvio_state_file': {'threads': 5},
            'make_misc_data': {'threads': 2},
            'anvi_import_everything': {'threads': 5},
            })

        # Directory structure for Snakemake workflow
        self.dirs_dict.update({"EXTRACTED_RIBO_PROTEINS_DIR": "ECO_PHYLO_WORKFLOW/01_REFERENCE_PROTEIN_DATA"})
        self.dirs_dict.update({"RIBOSOMAL_PROTEIN_FASTAS": "ECO_PHYLO_WORKFLOW/02_NR_FASTAS"})
        self.dirs_dict.update({"MSA": "ECO_PHYLO_WORKFLOW/03_MSA"})
        self.dirs_dict.update({"RIBOSOMAL_PROTEIN_MSA_STATS": "ECO_PHYLO_WORKFLOW/04_SEQUENCE_STATS"})
        self.dirs_dict.update({"TREES": "ECO_PHYLO_WORKFLOW/05_TREES"})
        self.dirs_dict.update({"MISC_DATA": "ECO_PHYLO_WORKFLOW/06_MISC_DATA"})
        self.dirs_dict.update({"SCG_NT_FASTAS": "ECO_PHYLO_WORKFLOW/07_SCG_NT_FASTAS"})
        self.dirs_dict.update({"RIBOSOMAL_PROTEIN_FASTAS_RENAMED": "ECO_PHYLO_WORKFLOW/08_RIBOSOMAL_PROTEIN_FASTAS_RENAMED"})


    def init(self):
        """This function is called from within the snakefile to initialize parameters."""

        super().init()

        # Re-assigning LOGS/ dir to inside ECO_PHYLO_WORKFLOW/ dir
        self.dirs_dict.update({"LOGS_DIR": "ECO_PHYLO_WORKFLOW/00_LOGS"})

        self.names_list = []
        self.names_dirs = []

        # Load metagenomes.txt
        self.metagenomes = self.get_param_value_from_config(['metagenomes'])
        self.input_dirs_dict = {}

        if self.metagenomes:
            filesnpaths.is_file_exists(self.metagenomes)
            try:
                self.metagenomes_df = pd.read_csv(self.metagenomes, sep='\t', index_col=False)
                self.metagenomes_name_list = self.metagenomes_df.name.to_list()
                self.metagenomes_path_list = self.metagenomes_df.contigs_db_path.to_list()
                self.metagenomes_dirname_list = [os.path.dirname(x) for x in self.metagenomes_path_list]
                self.input_dirs_dict.update(dict(zip(self.metagenomes_name_list, self.metagenomes_dirname_list)))
                self.names_dirs.extend(self.metagenomes_dirname_list)
                self.names_list.extend(self.metagenomes_name_list)

            except IndexError as e:
                raise ConfigError("The metagenomes.txt file, '%s', does not appear to be properly formatted. "
                                  "This is the error from trying to load it: '%s'" % (self.metagenomes_df, e))

        # Load external-genomes.txt
        self.external_genomes = self.get_param_value_from_config(['external_genomes'])
        if self.external_genomes:
            filesnpaths.is_file_exists(self.external_genomes)
            try:
                self.external_genomes_df = pd.read_csv(self.external_genomes, sep='\t', index_col=False)
                self.external_genomes_names_list = self.external_genomes_df.name.to_list()
                self.external_genomes_path_list = self.external_genomes_df.contigs_db_path.to_list()
                self.external_genomes_dirname_list = [os.path.dirname(x) for x in self.external_genomes_path_list]
                self.input_dirs_dict.update(dict(zip(self.external_genomes_names_list, self.external_genomes_dirname_list)))
                self.names_dirs.extend(self.external_genomes_dirname_list)
                self.names_list.extend(self.external_genomes_names_list)

            except IndexError as e:
                raise ConfigError("The external-genomes.txt file, '%s', does not appear to be properly formatted. "
                                  "This is the error from trying to load it: '%s'" % (self.external_genomes_df, e))
        else:
            self.external_genomes_names_list = []

        # Make a unique list
        self.names_dirs = list(set(self.names_dirs))

        # Make variables that tells whether we have metagenomes.txt, external-genomes.txt, or both
        if self.metagenomes and not self.external_genomes:
            self.mode = 'metagenomes'
        if not self.metagenomes and self.external_genomes:
            self.mode = 'external_genomes'
        if self.metagenomes and self.external_genomes:
            self.mode = 'both'

        # Load HMM list
        self.hmm_list_path = self.get_param_value_from_config(['hmm_list'])
        if self.hmm_list_path: 
            filesnpaths.is_file_exists(self.hmm_list_path)
            try:
                HMM_df = pd.read_csv(self.hmm_list_path, sep='\t', index_col=False)
                self.HMM_source_dict = dict(zip(HMM_df.name, HMM_df.source))
                self.HMM_path_dict = dict(zip(HMM_df.name, HMM_df.path))

            except IndexError as e:
                raise ConfigError("The hmm_list.txt file, '%s', does not appear to be properly formatted. "
                                  "This is the error from trying to load it: '%s'" % (self.hmm_list_path, e))

            if any("-" in s for s in self.HMM_source_dict.keys()):
                raise ConfigError(f"Please do not use "-" in your external HMM names in: "
                                  f"{self.hmm_list_path}. It will make our lives "
                                  f"easier with Snakemake wildcards :)")

        # FIXME: this line prints the list of HMM_sources to stdout and I don't want that
        self.internal_HMM_sources = list(anvio.data.hmm.sources.keys())

        for HMM, HMM_path in self.HMM_source_dict.items():
            if HMM_path == "INTERNAL":
                if self.HMM_source_dict[HMM] not in self.internal_HMM_sources:
                    HMM_source = self.HMM_source_dict[HMM]
                    raise ConfigError(f"{HMM_source} is not an 'INTERNAL' HMM source for anvi'o. "
                                      f"Please double check {self.hmm_list_path} to see if you spelled it right or "
                                      f"please checkout the default internal HMMs here: https://merenlab.org/software/anvio/help/7/artifacts/hmm-source/#default-hmm-sources")

        # Load samples.txt
        self.samples_txt_file = self.get_param_value_from_config(['samples_txt'])

        if not self.samples_txt_file:
            if os.path.exists('samples.txt'):
                self.samples_txt_file = 'samples.txt'
            else:
                raise ConfigError("Ehem. Your config file does not include a `samples_txt` directive. "
                                  "Anvi'o tried to assume that your `samples.txt` may be in your work "
                                  "directory, but you don't seem to have a `samples.txt` file anywhere "
                                  "around either. So please add a `samples.txt` directive.")

        filesnpaths.is_file_tab_delimited(self.samples_txt_file)
        try:
            # getting the samples information (names, [group], path to r1, path to r2) from samples.txt
            self.samples_information = pd.read_csv(self.samples_txt_file, sep='\t', index_col=False)
        except IndexError as e:
            raise ConfigError("Looks like your samples_txt file, '%s', is not properly formatted. "
                              "This is what we know: '%s'" % (self.samples_txt_file, e))
        if 'sample' not in list(self.samples_information.columns):
            raise ConfigError("Looks like your samples_txt file, '%s', is not properly formatted. "
                              "We are not sure what's wrong, but we can't find a column with title 'sample'." % self.samples_txt_file)

        self.sample_names_for_mapping_list = self.samples_information['sample'].to_list()

        # Pick which tree algorithm
        self.run_iqtree = self.get_param_value_from_config(['iqtree', 'run'])
        self.run_fasttree = self.get_param_value_from_config(['fasttree', 'run'])

        if not self.run_iqtree and not self.run_fasttree:
            raise ConfigError("Please choose either iqtree or fasttree in your config file to run your phylogenetic tree.")

        # Decide to clusterize metagenomic workflow
        self.clusterize_metagenomics_workflow = self.get_param_value_from_config(['run_metagenomics_workflow', 'clusterize'])
        self.metagenomics_workflow_HPC_string = self.get_param_value_from_config(['run_metagenomics_workflow', 'cluster_submission_params'])

        self.target_files = self.get_target_files()

    def get_target_files(self):
        target_files = []

        for HMM in self.HMM_source_dict.keys():

            target_file = os.path.join(self.dirs_dict['MISC_DATA'], f"{HMM}_misc.tsv")
            target_files.append(target_file)

            target_file = os.path.join("ECO_PHYLO_WORKFLOW/METAGENOMICS_WORKFLOW", "metagenomics_workflow.done")
            target_files.append(target_file)

            target_file = os.path.join("ECO_PHYLO_WORKFLOW", f"{HMM}_state_imported.done")
            target_files.append(target_file)

            target_file = os.path.join(self.dirs_dict['TREES'], f"{HMM}", f"{HMM}_renamed.nwk")
            target_files.append(target_file)

            target_files.append(target_file)

        return target_files