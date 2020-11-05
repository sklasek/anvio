#! /usr/bin/env Rscript

# To run this script you must have run `anvi-export-functions` and `anvi-export-gene-calls`:
# anvi-export-functions -c ST_809635352_CPS_0028.db --annotation-sources COG_FUNCTION -o ST_809635352_CPS_0028_functions.txt
# anvi-export-gene-calls -c ST_809635352_CPS_0028.db --gene-caller prodigal -o ST_809635352_CPS_0028_gene_calls.txt

# anvi-export-functions -c ST_764588959_CPS_0011.db --annotation-sources COG_FUNCTION -o ST_764588959_CPS_0011_functions.txt
# anvi-export-gene-calls -c ST_764588959_CPS_0011.db --gene-caller prodigal -o ST_764588959_CPS_0011_gene_calls.txt

# Load env
library(optparse)
library(tidyverse)
# library(gggenes)

option_list <- list(
  make_option(
    c("-c", "--contigsdb"),
    type="character",
    help="help"
  )
)

op <- OptionParser(
  option_list=option_list,
  description="Here is what the program does",
)
args <- parse_args(op)

args$contigsdb

# cleanfunction()


contig_db_list <- c("ST_809635352_CPS_0028.db", "ST_764588959_CPS_0011.db")

# Load data
get_files <- function(X) {
  ###
  # X <- contig_db_list[[1]]
  ###
  
  X <- str_remove(X, ".db")
  
  functions_path <- Sys.glob(paste0("/Users/mschechter/github/2018_Schmid_Shaiber_et_al_CPS/data/CONTIGSDB/", X,'*', '_functions.txt'))
  gene_calls_path <- Sys.glob(paste0("/Users/mschechter/github/2018_Schmid_Shaiber_et_al_CPS/data/CONTIGSDB/", X,'*', '_gene_calls.txt'))
  
  functions <- read_tsv(functions_path)
  gene_calls <- read_tsv(gene_calls_path)
  
  listy <- list(name = X, functions = functions, gene_calls = gene_calls)
}

contigs <- lapply(contig_db_list, get_files)

# Clean data for gggenes
prepare_to_plot <- function(X) {
  
  ###
  # X <- contigs[[2]]
  ###
  externalfunctions_clean <- X$functions %>% 
    filter(source != "COG_CATEGORY") %>% 
    select(gene_callers_id, accession, e_value) %>%
    mutate(fill = "black")
  
  contig_info <- X$gene_calls %>%
    mutate(direction = case_when(direction == FALSE ~ "f")) %>%
    left_join(externalfunctions_clean) %>%
    select(contig, accession, start, stop, direction, fill) %>%
    rename(molecule = contig ,gene = "accession", strand = direction, end = stop) %>%
    mutate(direction = gsub("f", "1", strand)) %>%
    mutate(direction = gsub("r", "-1", strand))
}

contigs_prepped <- lapply(contigs, prepare_to_plot)

# Rowbind all contig tables
contigs_final <- bind_rows(contigs_asdf)

# Plot
ggplot(contigs_final, aes(xmin = start, xmax = end, y = molecule, fill = gene, label = gene)) +
  geom_gene_arrow() +
  facet_wrap(~ molecule, scales = "free", ncol = 1) +
  scale_fill_brewer(palette = "Set3") +
  geom_gene_label(align = "left", # https://cran.r-project.org/web/packages/ggfittext/vignettes/introduction-to-ggfittext.html
                  color = "black",
                  min.size = 1,
                  position = "identity")

