import numpy as np
import pandas as pd


def load_adata_from_starsolo(
    input_dir,
    var_filename="features.tsv",  # other values: SJ.out.tab
):
    import scanpy as sc
    import os
    adata = sc.read_mtx(os.path.join(input_dir, "matrix.mtx")).T
    obs = pd.read_csv(os.path.join(input_dir, "barcodes.tsv"), header=None).set_index(0)
    obs.index.name = None
    var = pd.read_csv(
        os.path.join(input_dir, var_filename),
        sep="\t",
        header=None,
        names=[
            "chromosome",
            "start",
            "end",
            "strand",
            "intron_motif",
            "annotated",
            "total_unique_mapping",
            "total_multi_mapping",
            "max_overhang",
        ],
    )
    var.strand.replace({0: "NA", 1: "+", 2: "-"}, inplace=True)
    var.index = var.chromosome.astype(str) + ":" + var.start.astype(str) + "-" + var.end.astype(str)

    adata.obs = obs
    adata.var = var
    print("Filtering out undefined strand.")
    adata = adata[:, adata.var.strand != "NA"]
    return adata


def add_gene_annotation(adata, gtf_path, filter_unique_gene=True):
    import warnings
    if not isinstance(gtf_path, str):
        gtf_path = str(gtf_path)
    if not gtf_path.endswith("110.gtf"):
        warnings.warn("The file path does not end with '110.gtf'", UserWarning)
    adata_out = adata.copy()
    gtf = pd.read_csv(
        gtf_path,
        sep="\t",
        header=None,
        comment="#",
        names=[
            "chromosome",
            "source",
            "feature",
            "start",
            "end",
            "score",
            "strand",
            "frame",
            "attribute",
        ],
        dtype={'chromosome': str}
    )

    gtf = gtf[gtf.feature == "exon"]
    gtf["gene_id"] = gtf.attribute.str.extract(r'gene_id "([^;]*)";')
    gtf["gene_name"] = gtf.attribute.str.extract(r'gene_name "([^;]*)";')
    gtf["canonical"] = gtf["attribute"].str.findall("Ensembl_canonical").apply(lambda x: len(x) > 0)

    if np.array([x.startswith("chr") for x in adata_out.var.chromosome.unique()]).sum() != 0:
        gtf.chromosome = "chr" + gtf.chromosome.astype(str)
    else:
        pass
    gene_id_name = gtf[["gene_id", "gene_name"]].drop_duplicates()

    exon_starts = (
        gtf[["chromosome", "start", "gene_id", "canonical"]].copy().rename(columns={"start": "pos"})
    )
    exon_starts.pos -= 1
    exon_ends = (
        gtf[["chromosome", "end", "gene_id", "canonical"]].copy().rename(columns={"end": "pos"})
    )
    exon_ends.pos += 1
    exon_boundaries = pd.concat(
        [exon_starts, exon_ends], ignore_index=True
    )
    genes_by_exon_boundary = exon_boundaries.groupby(["chromosome", "pos"])\
        .agg({"canonical": "any", "gene_id": lambda x: x.unique()})

    adata_out.var = (
        adata_out.var.merge(
            genes_by_exon_boundary,
            how="left",
            left_on=["chromosome", "start"],
            right_on=["chromosome", "pos"],
        )
        .rename(columns={"gene_id": "gene_id_start", "canonical": "canonical_start"})
        .set_index(adata_out.var.index)
    )
    adata_out.var = (
        adata_out.var.merge(
            genes_by_exon_boundary,
            how="left",
            left_on=["chromosome", "end"],
            right_on=["chromosome", "pos"],
        )
        .rename(columns={"gene_id": "gene_id_end", "canonical": "canonical_end"})
        .set_index(adata_out.var.index)
    )

    def fill_na_with_empty_array(val):
        return val if isinstance(val, np.ndarray) else np.array([])
    def fill_na_with_empty_string(val):
        return val if not np.isnan(val) else ""    
    adata_out.var.gene_id_start = adata_out.var.gene_id_start.apply(fill_na_with_empty_array)
    adata_out.var.gene_id_end = adata_out.var.gene_id_end.apply(fill_na_with_empty_array)
    adata_out.var.canonical_start = adata_out.var.canonical_start.apply(fill_na_with_empty_string)
    adata_out.var.canonical_end = adata_out.var.canonical_end.apply(fill_na_with_empty_string)
    adata_out.var["gene_id_list"] = adata_out.var.apply(
        lambda row: np.unique(np.concatenate([row.gene_id_start, row.gene_id_end])),
        axis=1,
    )
    adata_out.var["n_genes"] = adata_out.var.gene_id_list.apply(len)
    adata_out.var.gene_id_list = adata_out.var.gene_id_list.apply(
        lambda x: ",".join(x.tolist())
    )
    adata_out.var.gene_id_start = adata_out.var.gene_id_start.apply(
        lambda x: ",".join(x.tolist())
    )
    adata_out.var.gene_id_end = adata_out.var.gene_id_end.apply(
        lambda x: ",".join(x.tolist())
    )

    if filter_unique_gene:
        print("Filtering to introns associated to 1 and only 1 gene.")
        adata_out = adata_out[:, adata_out.var.n_genes == 1]
        adata_out.var["gene_id"] = adata_out.var.gene_id_list
        adata_out.var.drop(columns=["gene_id_list",], inplace=True)
        adata_out.var = adata_out.var.merge(gene_id_name, how="left", on="gene_id").set_index(
            adata_out.var.index
        )
        adata_out.var.index = adata_out.var.gene_name.astype(str) + "_" + adata_out.var.index.astype(str)
    return adata_out


def group_introns(adata, by="three", filter_unique_gene_per_group=True):
    adata_out = adata.copy()
    if by == "three":
        adata_out.var["intron_group"] = adata_out.var.apply(
            lambda intron: intron.chromosome
            + "_"
            + (str(intron.end) if intron.strand == "+" else str(intron.start))
            + "_"
            + intron.strand,
            axis=1,
        )
    elif by == "five":
        adata_out.var["intron_group"] = adata_out.var.apply(
            lambda intron: intron.chromosome
            + "_"
            + (str(intron.end) if intron.strand == "-" else str(intron.start))
            + "_"
            + intron.strand,
            axis=1,
        )
    elif by == "gene":
        adata_out.var["intron_group"] = adata_out.var.gene_id
    else:
        raise Exception(f"Grouping by {by} not yet supported.")

    intron_group_sizes = (
        adata_out.var.intron_group.value_counts()
        .rename("intron_group_size")
        .to_frame()
    )
    adata_out.var = adata_out.var.merge(
        intron_group_sizes, how="left", left_on="intron_group", right_index=True
    ).set_index(adata_out.var.index)
    print("Filtering singletons.")
    adata_out = adata_out[:, adata_out.var.intron_group_size > 1]


    if filter_unique_gene_per_group:
        print("Filtering intron groups associated with more than 1 gene.")
        n_genes_per_intron_group = adata_out.var.groupby("intron_group").gene_id.nunique().to_frame().rename(columns={"gene_id": "n_genes_per_intron_group"})
        adata_out.var = adata_out.var.merge(n_genes_per_intron_group, how="left", left_on="intron_group", right_index=True)
        adata_out = adata_out[:, adata_out.var.n_genes_per_intron_group==1]
        adata_out.var.intron_group = adata_out.var.gene_name.astype(str) + "_" + adata_out.var.intron_group.astype(str)

    return adata_out


def make_intron_group_summation_cpu(intron_groups):
    import scipy.sparse as sp_sparse
    intron_groups = relabel(intron_groups)
    n_introns = len(intron_groups)
    n_intron_groups = len(np.unique(intron_groups))
    rows, cols = zip(*list(enumerate(intron_groups)))
    vals = np.ones(n_introns, dtype=int)
    intron_group_summation = sp_sparse.coo_matrix(
        (vals, (rows, cols)), (n_introns, n_intron_groups)
    ).tocsr()
    return intron_group_summation


def relabel(labels):
    all_old_labels = pd.unique(labels).tolist()
    mapping = {c: i for i, c in enumerate(all_old_labels)}
    new_labels = np.array([mapping[l] for l in labels])
    return new_labels


def group_normalize(X, groups, smooth=False):
    '''
    group normalize X by groups

    Args:
        X: np.ndarray
            ttype x absolute intron count matrix
        groups: np.ndarray
            intron group labels
        smooth: bool
            whether to smooth the group normalization
    
    Returns:
        PSI: np.ndarray
            ttype x PSI matrix

    '''
    assert type(X) == np.ndarray, "X must be a numpy array."
    assert type(groups) == np.ndarray, "groups must be a numpy array."
    assert X.shape[1] == len(groups), "X must have the same number of columns as the length of groups."
    groups = relabel(groups)
    intron_group_summation = make_intron_group_summation_cpu(groups)
    if smooth:
        intron_sum = X.sum(axis=0)
        intron_group_sum = intron_sum @ intron_group_summation
        with np.errstate(divide='ignore'):
            X = X + intron_sum / intron_group_sum[groups]
    intron_group_sums = X @ intron_group_summation
    return X / intron_group_sums[:,groups]


def calculate_PSI(adata, smooth=False):
    if isinstance(adata.X, np.ndarray):
        out = group_normalize(adata.X, adata.var.intron_group.values.to_numpy(), smooth=smooth)
    else:
        out = group_normalize(adata.X.toarray(), adata.var.intron_group.values.to_numpy(), smooth=smooth)
    return out

def filter_min_global_SJ_counts(adata, min_SJ_counts):
    idx_features = np.flatnonzero(adata.X.sum(axis=0) >= min_SJ_counts)
    # print(f"{(len(idx_features)/adata.X.shape[1]) * 100}% of introns passed the filter")
    return adata[:, idx_features]

def filter_min_cells_per_feature(adata, min_cells_per_feature, idx_cells_to_count=slice(None)):
    print("filter_min_cells_per_feature")
    if isinstance(adata.X, np.ndarray):
        idx_features = np.where((adata.X[idx_cells_to_count] > 0).sum(axis=0) >= min_cells_per_feature)[0]
    else:
        idx_features = np.where((adata.X[idx_cells_to_count] > 0).sum(axis=0).A1 >= min_cells_per_feature)[0]
    adata = adata[:, idx_features]
    adata = filter_singletons(adata)
    return adata


def filter_min_cells_per_intron_group(adata, min_cells_per_intron_group, idx_cells_to_count=slice(None)):
    print("filter_min_cells_per_intron_group")
    intron_groups = relabel(adata.var.intron_group.values)
    intron_group_summation = make_intron_group_summation_cpu(intron_groups)
    if isinstance(adata.X, np.ndarray):
        n_cells_per_intron_group = ((((adata.X[idx_cells_to_count]) @ intron_group_summation) > 0).sum(axis=0))
    else:
        n_cells_per_intron_group = ((((adata.X[idx_cells_to_count]) @ intron_group_summation) > 0).sum(axis=0)).A1
    idx_intron_groups = np.where(n_cells_per_intron_group >= min_cells_per_intron_group)[0]
    idx_features = np.where(np.isin(intron_groups, idx_intron_groups))[0]
    adata = adata[:, idx_features]
    adata = filter_singletons(adata)
    return adata


def filter_singletons(adata):
    from collections import Counter
    print("filter_singletons")
    intron_group_counter = Counter(adata.var.intron_group.values)
    intron_group_counts = np.array([intron_group_counter[c] for c in adata.var.intron_group.values])
    idx_features = np.where(intron_group_counts > 1)[0]
    adata = adata[:, idx_features]
    return adata


def filter_min_global_proportion(adata, min_global_proportion, idx_cells_to_count=slice(None)):
    print("filter_min_global_proportion")
    intron_groups = relabel(adata.var.intron_group.values)
    intron_group_summation = make_intron_group_summation_cpu(intron_groups)
    if isinstance(adata.X, np.ndarray):
        feature_counts = adata.X[idx_cells_to_count].sum(axis=0)
    else:
        feature_counts = adata.X[idx_cells_to_count].sum(axis=0).A1
    intron_group_counts = (feature_counts @ intron_group_summation)
    feature_proportions = feature_counts / intron_group_counts[intron_groups]
    adata = adata[:, feature_proportions >= min_global_proportion]
    adata = filter_singletons(adata)
    return adata


def regroup(adata):
    from collections import defaultdict
    import networkx as nx
    print("regroup")
    A = adata.var.start.values
    B = adata.var.end.values
    intron_groups = adata.var.intron_group.values.astype(object)

    mask = np.zeros(len(adata.var), dtype=bool)

    intron_group_introns = defaultdict(list)
    for i, c in enumerate(intron_groups):
        intron_group_introns[c].append(i)

    all_intron_groups = np.unique(intron_groups)
    intron_group_counter = Counter(intron_groups)

    for c in pd.unique(intron_groups):
        counts = intron_group_counter[c]
        if counts < 2: continue
        G = nx.Graph()
        nodes = intron_group_introns[c]
        for node in nodes:
            G.add_node(node)
        for n1 in nodes:
            for n2 in nodes:
                if n1 < n2:
                    if A[n1] == A[n2] or B[n1] == B[n2]:
                        G.add_edge(n1, n2)
        connected_components = list(nx.connected_components(G))
        for i, connected_component in enumerate(connected_components):
            if len(connected_component) < 2: continue
            for intron_id in connected_component:
                mask[intron_id] = True
                intron_groups[intron_id] = str(intron_groups[intron_id]) + '_'+ str(i)

    adata = adata[:, mask]

    return adata