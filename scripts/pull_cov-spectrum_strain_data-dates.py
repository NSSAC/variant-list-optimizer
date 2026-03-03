#!/usr/bin/env python3
# coding: utf-8

import pandas as pd
import numpy as np
import requests
import json
import time

from sys import stderr
from datetime import datetime
from datetime import date
from statsmodels.stats.proportion import proportion_confint

from pango_aliasor.aliasor import Aliasor

from pathlib import Path
from functools import cache
import typer
typer.main.get_command_name = lambda name: name # override click changing underscores to dashes
from typing_extensions import Annotated

app = typer.Typer()

vocs = (
    'B.1.1.7',
    'BA.1',
    'BA.1.1',
    'BA.2',
    'BA.2.12.1',
    'BA.4',
    'BA.4.1',
    'BA.4.6',
    'BA.5',
    'BA.5.1',
    'BA.5.2',
    'BA.5.2.1',
    'BA.2.75',
    'BA.2.75.1',
    'BA.2.75.2',
    'BF.7',
    'B.1.1.529',
    'B.1.351',
    'P.1',
    'B.1.617.1',
    'B.1.617.2',
    'B.1.617.3',
    'B.1.621',
    'CA.1',
    'BQ.1.1',
    'BN.1',
    'XBB',
    )

states = (
    'US', '01', '02', '04', '05', '06', '08', '09', '10', '11', '12', '13',
    '15', '16', '17', '18', '19', '20', '21', '22', '23', '24', '25', '26',
    '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38',
    '39', '40', '41', '42', '44', '45', '46', '47', '48', '49', '50', '51',
    '53', '54', '55', '56', '66', '69', '72', '78',
    )

@cache
def proc_state_lookup(data_path="/scif/apps/State_ST_fips.csv"):
    """
    Create some useful lookup dictionaries from a data csv with . . .
    State,ST,fips
    """
    state_to_stfips_df = pd.read_csv(data_path, header=None, dtype={"2":str})
    state_to_stfips_df[2] = state_to_stfips_df[2].astype(str).str.zfill(2)
    state_to_stfips = state_to_stfips_df.set_index(0)[2].to_dict()
    state_to_state_abbr = state_to_stfips_df.set_index(0)[1].to_dict()
    stfips_to_state_abbr = state_to_stfips_df.set_index(2)[1].to_dict()
    return state_to_stfips, state_to_state_abbr, stfips_to_state_abbr


def decorate_prevalence(df):
    """
    Assumes the df is one geographic surveillance bucket and all prevalences of variants will be calculated relative to that
    """
    #get the daily fraction just for fun / sanity check
    day_counts=df.pivot_table(index=["date"], columns = "lineage", values = "lineage_count", aggfunc=sum).fillna(0)
    day_prev = (day_counts.T / day_counts.sum(axis=1))
    df['daily_frac'] = [day_prev.loc[row,col] for row, col in tuple(zip(df["lineage"], df["date"]))]
    #"lineage_count_rolling", "7 day rolling average of samples assigned to the Pango lineage on that day",
    lcount_rolling = df.pivot(index="date", columns="lineage", values="lineage_count").fillna(0).rolling(7).mean().fillna(0).T
    df['lineage_count_rolling'] = [lcount_rolling.loc[row,col] for row, col in tuple(zip(df["lineage"], df["date"]))]
    #"total_count", "Total number of samples sequenced on a given day"
    total_samples=df.pivot(index="date", columns="lineage", values="lineage_count").sum(axis=1).fillna(0).T
    df["total_count"]=[total_samples.loc[row] for row in df["date"]]
    #"total_count_rolling", "7 day rolling average of total samples sequenced on that day"
    rolling_total=total_samples.rolling(7).mean().fillna(0)
    df['total_count_rolling'] = [rolling_total.loc[row] for row in df["date"]]
    #"proportion", "Estimated prevalence of a variant: lineage_count_rolling / total_count_rolling",
    df["proportion"]=(df["lineage_count_rolling"]/df["total_count_rolling"]).fillna(0)
    #"proportion_ci_lower", "95% confidence interval lower bound of estimated prevalence of a variant, calculated using Jeffrey's interval",
    #https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9258294/
    #https://outbreak.info/situation-reports/methods
    ci_list=[proportion_confint(lineage, total, method="jeffreys") if total > 0 else (0.0,1.0) for lineage, total in tuple(zip(df["lineage_count_rolling"],df["total_count_rolling"]))]
    df["proportion_ci_lower"], df["proportion_ci_upper"] = zip(*ci_list)
    return df

def get_state_time_series_spectrum(division, dateFrom="2020-03-01", dateTo=None):
    """
    Get aggregate variant information per state
    """
    if dateTo is None:
        dateTo = date.today().isoformat()
#     print(state, strain)
    ###  Andrew's approved URL: 
    ###  https://lapis.cov-spectrum.org/open/v2/sample/aggregated
    ###  ?dateFrom=2024-01-01&country=USA&division=Virginia&fields=date,nextcladePangoLineage,division
    time.sleep(2)
    base_url = "https://lapis.cov-spectrum.org/open/v2/sample/aggregated"

    headers = {"Accept": "application/json, text/plain, */*", 
           "Accept-Encoding": "gzip, deflate, br"}
    
    form_data = {
##        "host": "Human",
        "dateFrom": dateFrom,
        "dateTo": dateTo,
        "division": division,
        "country": "USA",
##        "accessKey": "9Cb3CqmrFnVjO3XCxQLO6gUnKPd",
        "fields": "date,nextcladePangoLineage,division",
    }
    
    response = requests.get(base_url, headers = headers, params=form_data)
    try:
        response = json.loads(response.text)
    except Exception as E:
        print(f"problem getting {division}", file=stderr)
        print(f"{response.status_code=}", file=stderr)
#         time.sleep(10)
#         continue
        raise ValueError(E)

    x = pd.DataFrame(response.get("data"))
#     import pdb; pdb.set_trace()
    if x.empty:
        print(f"No data for {division} between {dateFrom} and {dateTo}")
    else:
        x.date = pd.to_datetime(x.date)
        x.date = x.date.apply(lambda row: pd.to_datetime(row, format="%Y-%dm-%d").date())
        x=x.rename(columns={"nextcladePangoLineage":"lineage","count":"lineage_count"}).sort_values(by='date',ascending=True)
    
    return x




def run_spectrum(state_to_stfips, dateFrom="2020-03-01", dateTo=None):
    if dateTo is None:
        dateTo = date.today().isoformat()
    states_info=[]
    replace={"District of Columbia":"Washington DC"}
    for r,n in replace.items():
        state_to_stfips[n]=state_to_stfips.pop(r)
    forbidden=["American Samoa", "Guam", "Northern Mariana Islands", "Puerto Rico", "Virgin Islands"]
    state_num=0
    for i,j in state_to_stfips.items():
        if i not in forbidden:
            state_num+=1
            print(f"{state_num}{i}")
            x = get_state_time_series_spectrum(i, dateFrom=dateFrom, dateTo=dateTo)
            #x["lineage"]=x.lineage_orig
            #other_test=set([j for i in voc_partition.values() for j in i]+list(voc_partition.keys()))
            #x.loc[~x["lineage"].isin(other_test),"lineage"]="Other"
            #for k,v in voc_partition.items():
            #    x["lineage"]=x["lineage"].replace(v,k)
            #x=x.groupby(['date','lineage']).agg({'lineage_count': 'sum',}).reset_index()
            #x=decorate_prevalence(x)
            x["fips"]=j #x['division'].map(state_to_stfips)
            states_info.append(x)
    states_table=pd.concat(states_info)
    us_table = states_table.groupby(['date','lineage']).agg({'lineage_count': 'sum'}).reset_index()
    us_table["fips"]="US"
    us_table=decorate_prevalence(us_table)
    results=pd.concat([states_table,us_table]).sort_values(by=['fips','lineage','date'],ascending=True)
    return results


@app.command()
def main(
    st_lookup_csv: Annotated[str, typer.Option(help="State, ST, fips csv path")] = "data/State_ST_fips.csv",
    us_states: Annotated[str, typer.Option(help="(comma-separated list of )state abbreviation(s)")] = None,
    outdir: Annotated[str, typer.Option(help="output directory")] = 'data/',
    config: Annotated[str, typer.Option(help="json config file path")] = "vtp_config.json",
    dateFrom: Annotated[str, typer.Option(help="Start date for API query in YYYY-MM-DD format")] = "2020-03-01",
    dateTo: Annotated[str, typer.Option(help="End date for API query in YYYY-MM-DD format (default: today)")] = None,
):
    """
    VOCs for Omicron updated by Bryan on Sept 8th using "editors choice" from: https://cov-spectrum.org/explore/United%20States/AllSamples/Past6M

        dict = tribble(~"API Field", ~Documentation,
                    "date", "Sequence collection date, in YYYY-MM-DD format",
                    "lineage", "Pango lineage",
                    "lineage_count", "Total number of samples on a given day assigned to the Pango lineage",
                    "lineage_count_rolling", "7 day rolling average of samples assigned to the Pango lineage on that day",
                    "prevalence", "Estimated prevalence of the variant on that day",
                    "prevalence_rolling", "7 day rolling average estimate of prevalence of the variant on that day",
                    "proportion", "Estimated prevalence of a variant: lineage_count_rolling / total_count_rolling",
                    "proportion_ci_lower", "95% confidence interval lower bound of estimated prevalence of a variant, calculated using Jeffrey's interval",
                    "proportion_ci_upper", "95% confidence interval upper bound of estimated prevalence of a variant, calculated using Jeffrey's interval",
                    "total_count", "Total number of samples sequenced on a given day",
                    "total_count_rolling", "7 day rolling average of total samples sequenced on that day"
                    )
    """
    config_path = Path(config)
    config = dict()
    if config_path.exists():
        with config_path.open() as fh:
            config = json.load(fh)

    if outdir:
        config["outdir"] = outdir
    else:
        config.setdefault("outdir", "data/")

    if us_states:
        config["us_states"] = us_states.split(",")
    else:
        config.setdefault("us_states", ["VA"])


    outdir = config["outdir"]
    us_states = [s.upper() for s in config["us_states"]]
    print(f"{outdir = }")
    print(f"{us_states = }")

    namer=Aliasor()
    namer.enable_expansion()

    #instead of prefixes check for proper subsets of expansions, if they exclude them all
    voc_partition = namer.partition_focus(vocs,remove_self=False)
    #voc_dependence=namer.map_dependent(vocs)

    outdir_path = Path(outdir)
    outdir_path.mkdir(exist_ok=True, parents=True)

    query_log_path = outdir_path / "query_log.txt"
    query_log_path.open("w").write(json.dumps({k:",".join(v) for k,v in voc_partition.items()}, indent=4, separators=(',', ':')))

    state_to_stfips, _, _ = proc_state_lookup(st_lookup_csv)

    results=run_spectrum(state_to_stfips, dateFrom=dateFrom, dateTo=dateTo)
    csv_path = outdir_path / f"cov-spectrum_variants_states_long_{dateFrom}_to_{dateTo}.csv"

    results.to_csv(str(csv_path), index=False)

if __name__ == "__main__":
    app()
