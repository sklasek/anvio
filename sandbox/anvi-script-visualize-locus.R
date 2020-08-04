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


