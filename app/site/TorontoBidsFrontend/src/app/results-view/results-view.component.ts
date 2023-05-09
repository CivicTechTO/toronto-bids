import { Component, OnInit } from '@angular/core';
import { ApiServiceService } from '../api-service.service';
import { SearchResult } from '../models/models';
@Component({
  selector: 'app-results-view',
  templateUrl: './results-view.component.html',
  styleUrls: ['./results-view.component.less']
})
export class ResultsViewComponent implements OnInit {

  results : SearchResult[] = [];
  constructor(private apiService:ApiServiceService) {
    this.resultsTextFilter= "";
    this.results = apiService.getSearchResults();
    console.log(this.results);
  }
  resultsTextFilter:string;

  ngOnInit(): void {
  }

}
