An HMM-list is a tsv file that contains the names and paths to %(hmm-source)s.

Here are the columns you will need:
- name: This is the name of your HMM. For example, if you are using the %(hmm-source) Bacteria_71 then the name of a HMM within that collection is `Ribosomal_S20`. However, if you are using an [external %(hmm-source)](https://anvio.org/help/main/artifacts/hmm-source/#user-defined-hmm-sources) then you must used the names defined in your `genes.txt`.
- source: This is the source of your HMM. [Some anvio default HMM sources](https://anvio.org/help/main/artifacts/hmm-source/#default-hmm-sources) include Bacteria_71 and Archaea_76. Again, if you are using [external %(hmm-source)](https://anvio.org/help/main/artifacts/hmm-source/#user-defined-hmm-sources) then you must used the names defined in your `genes.txt`.
- path: This is the path to your HMM. If you are using [anvio internal HMM sources](https://anvio.org/help/main/artifacts/hmm-source/#default-hmm-sources) please input "INTERNAL". If you are using [external %(hmm-source)](https://anvio.org/help/main/artifacts/hmm-source/#user-defined-hmm-sources) then please input the path to the directory that contains the HMM.

|name|source|path|
|:--|:--|:--|
|HMM_01|anvio_HMM_source|INTERNAL|
|Ribosomal_S20p|Bacteria_71|INTERNAL|
|HMM_03|external_HMM_source|/path/to/external-hmm-dir-03.db|
|(...)|(...)|

Here's a quick way to make it an HMM-list on the BASH command line
{{ codestart }}
echo -e "name\tsource\tpath" > hmm_list.txt
echo -e "Ribosomal_L16\tBacteria_71\tINTERNAL" >> hmm_list.txt
{{ codestop }}