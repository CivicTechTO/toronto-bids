import { Component, OnInit } from '@angular/core';
import { ApiServiceService } from '../api-service.service';
import { Buyer, SearchResult } from '../models/models';
import { faAngleDown } from '@fortawesome/free-solid-svg-icons';
import * as $ from 'jquery';

@Component({
  selector: 'app-results-view',
  templateUrl: './results-view.component.html',
  styleUrls: ['./results-view.component.less']
})
export class ResultsViewComponent implements OnInit {
  // sortByList : {display:string, value:number}[] = [{display:'A-Z',value:1},{display:'Posting Date',value:2},{display:'Closing Date',value:2}]
  sortByValue : string = "Posting Date";
  results : SearchResult[] = [];
  resultsSortClone : SearchResult[] = [];
  resultsClone : SearchResult[] = [];
  viewMore : boolean[] = [];
  buyersPerResult:Map<number,Buyer[]>;
  textFilter : string = "";
  displayedColumns: string[] = ['name', 'phone', 'location', 'email'];

  constructor(private apiService:ApiServiceService) {
    this.buyersPerResult = new Map<number,Buyer[]>();
    this.resultsTextFilter= "";
    this.results = apiService.getSearchResults();
    for (let i = 0 ; i < this.results.length ; i++){
      this.viewMore.push(false);
      this.buyersPerResult.set(i,this.results[i].buyers);
    }
  }
  resultsTextFilter:string;

  sortByList = [
      { id: 1, name: 'A-Z' },
      { id: 2, name: 'Posting Date' },
      { id: 3, name: 'Closing Date' },
  ];

  searchInProgress:boolean=false; 
  ngOnInit(): void {
   
  }
  loadData(){
    this.results = this.apiService.getSearchResults();
  }

  searchResults(){
    if (this.resultsTextFilter === ""){
      this.resetSearch();
      return;
    }
    this.resultsClone = $.extend(true,this.results);
    for(let i = this.results.length - 1 ; i >= 0 ; i--){
      if (!this.searchResultForText(this.resultsTextFilter,this.results[i])){
        this.results.splice(i,1);
      }
    }
    this.searchInProgress = true;
  }
  
  searchResultForText(text:string,result:SearchResult){
    return (result.commodity.indexOf(text) >= 0 || result.type.indexOf(text) >= 0 || result.commodity_type.indexOf(text) >= 0 || result.division.indexOf(text) >= 0 || result.short_description.indexOf(text) >= 0)
  }

  resetSearch(){
    this.resultsTextFilter="";
    this.loadData();
  }
}
