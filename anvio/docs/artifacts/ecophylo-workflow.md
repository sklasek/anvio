The EcoPhylo workflow is a [Snakemake](https://snakemake.readthedocs.io/en/stable/) workflow run by the anvi'o program, %(anvi-run-workflow)s. The goal of this workflow is to extract genes from NGS assemblies (metagenomes, Single-amplified genomes (SAGs), Metagenome-assembled genomes (MAGs)) to profile their ecology and calculate their evolutionary relationships. A popular application of EcoPhylo is to leverage [single-copy core genes](https://anvio.org/vocabulary/#single-copy-core-gene-scg) to profile the ecology and evolutionary relationships of microbial communities. However, one could also track other functional genes such as nifH or CAZymes. Another feature of this workflow is one can contextutalize genomes in the context of metagenomes to investigate their environmental relevence. 

## Input files

Like all workflows in anvi'o, EcoPhylo needs a %(workflow-config)s to get started. This file let's you customize the workflow to fit your science! Here is how you can that file:

{{ codestart }}
anvi-run-workflow -w ecophylo --get-default-config ecophylo_config.json
{{ codestop }}


EcoPhylo has two modes, [tree-mode](#tree-mode) and [profile-mode](#tree-mode). In `tree-mode`, the workflow will extract your gene of interest from all provided assemblies and calculate a phylogenetic tree. In `profile-mode`, `tree-mode` will be performed and sequences will be profiled using the provided raw metagenome sequences. The way EcoPhylo knows which mode you want to run is by checking which input files you pointed to in your %(workflow-config)s:

Input file requirements for each mode:

- `tree-mode`: %(hmm-list)s and at least one assembly source (%(metagenomes)s, %(external-genomes)s). 

- `profile-mode`: %(samples-txt)s AND `tree-mode` input requirements

## Running EcoPhylo

**Check out the DAG file**

Before you start a [Snakemake workflow](https://snakemake.readthedocs.io/en/stable/) it is recommend to check out the DAG file. This is a network representation of what the workflow is about to do. Here's how you get that picture: 

{{ codestart }}
anvi-run-workflow -w ecophylo -c ecophylo_config.json --save-workflow-graph
{{ codestop }}

**Run EcoPhylo**

Once you are happy with what the workflow is about to do this is how you execute the workflow!

{{ codestart }}
anvi-run-workflow -w ecophylo -c ecophylo_config.json
{{ codestop }}

**SLURM execution**

Here is the way to run this workflow on an HPC with SLURM. Please not that you will need to install [clusterize](https://github.com/ekiefl/clusterize)

{{ codestart }}
clusterize "anvi-run-workflow -w ecophylo -c ecophylo_config.json --additional-params --cluster \"clusterize -j={rule} -o={log} -n={threads} -R 100  -x --exclude \'\' \" --cores 100 --resource nodes=100 --latency-wait 100 --keep-going --rerun-incomplete" --exclude \'\'
{{ codestop }}

**Visualize**

This is how you start the EcoPhylo interactive interface once the workflow has completed. Please note that you need to be within the `ECOPHYLO_WORKFLOW` directory structure and to use the command that corresponded to the EcoPhylo mode you executed: `tree-mode` or `profile-mode`

`tree-mode`

{{ codestart }}
anvi-interactive -p ECOPHYLO_WORKFLOW/05_TREES/Ribosomal_L16/Ribosomal_L16-PROFILE.db -t ECOPHYLO_WORKFLOW/05_TREES/Ribosomal_L16/Ribosomal_L16_renamed.nwk --manual
{{ codestop }}

`profile-mode`

{{ codestart }}
anvi-interactive -p ECOPHYLO_WORKFLOW/METAGENOMICS_WORKFLOW/06_MERGED/Ribosomal_L16/PROFILE.db -c ECOPHYLO_WORKFLOW/METAGENOMICS_WORKFLOW/03_CONTIGS/Ribosomal_L16-contigs.db
{{ codestop }}
