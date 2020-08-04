#! /usr/bin/env Rscript
library(optparse)

option_list <- list(
    make_option(
        c("-e", "--externalgenecalls"),
        type="character",
        help="help"
    ),
    make_option(
        c("-f", "--externalfunctions"),
        type="character",
        help="help"
    )
)

op <- OptionParser(
    option_list=option_list,
    description="Here is what the program does",
)
args <- parse_args(op)

args$externalgenecalls


# Load env
library(tidyverse)
library(gggenes)

# Import data
# gene_calls <- read_tsv("Bfragilis_0001_CPS_0005-gene-calls.txt")
functions <- read_tsv("Bfragilis_0001_CPS_0005-functions.txt")
metagenomic_contig_orf_coord <- read_tsv("Bfragilis_0001_CPS_0005_contig_orf_coord.tsv")

# COG colors
functions_prep <- functions %>% 
    filter(source != "COG_CATEGORY") %>% 
    select(gene_callers_id, accession, e_value) %>%
    mutate(fill = "black")

# # functions_prep$accession %>% unique() 
# library("RColorBrewer")
# 
# num_colors <- functions %>% 
#   filter(source != "COG_CATEGORY") %>% 
#   select(gene_callers_id, accession, e_value) %>%
#   .$accession %>% unique() %>% length()
# 
# brewer.pal(n = num_colors, name = "RdBu")


# Join
example_genes

contig_info <- metagenomic_contig_orf_coord %>% 
    left_join(functions_prep) %>%
    select(contig, accession, start, stop, direction, fill) %>%
    rename(molecule = contig ,gene = "accession", strand = direction, end = stop) %>%
    # mutate(col = "black",
    #        lty='1',
    #        lwd='1',
    #        pch='8',
    #        cex='1',
    #        gene_type='arrows') %>%
    mutate(direction = gsub("f", "1", strand)) %>%
    mutate(direction = gsub("r", "-1", strand)) 
# mutate(lwd = as.numeric(lwd)) %>%
# mutate(lty = as.numeric(lty)) %>%
# mutate(pch = as.numeric(pch)) %>%
# mutate(cex = as.numeric(cex)) 

contig_info %>%
    ggplot(aes(xmin = start,
               xmax = end,
               y = molecule,
               fill = fill,
               label = gene,
               forward = strand
    )) + # important shit
    geom_gene_arrow(arrowhead_height = unit(7, "mm"),
                    arrowhead_width = unit(3, "mm"),
                    colour = "white") +
    geom_gene_label(align = "center", # https://cran.r-project.org/web/packages/ggfittext/vignettes/introduction-to-ggfittext.html
                    color = "black",
                    min.size = 1,
                    position = "identity") +
    facet_wrap(~ molecule, scales = "free", ncol = 1) +
    theme_genes() +
    scale_fill_brewer() +
    theme(plot.background = element_rect(fill = "#222222"), # plot background color
          legend.background = element_rect(fill = "#222222"), # legend background color
          legend.text=element_text(color="white"), # legend text color
          legend.title =element_text(color = "white"), # legend title color
          axis.title.x = element_text(colour = "white"), # x-axis text color
          axis.title.y = element_text(colour = "white"), # y-axis text color
          axis.line.x = element_line(color = "white"),
          axis.text.x = element_text(color="white"),
          axis.text.y = element_text(color="white"),
          legend.position="bottom") +
    ylab("Genomes") + # y-axis color
    labs(fill = "Genes") # Legend title


library(ggplot2)
library(gggenes)
ggplot(contig_info, aes(xmin = start, xmax = end, y = molecule, fill = gene, label = gene)) +
    geom_gene_arrow() +
    facet_wrap(~ molecule, scales = "free", ncol = 1) +
    scale_fill_brewer(palette = "Set3") +
    geom_gene_label(align = "left", # https://cran.r-project.org/web/packages/ggfittext/vignettes/introduction-to-ggfittext.html
                    color = "black",
                    min.size = 1,
                    position = "identity")


example_genes <- example_genes %>% as_tibble()