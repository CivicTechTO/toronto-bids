import { Component, OnInit } from '@angular/core';
@Component({
  selector: 'app-results-view',
  templateUrl: './results-view.component.html',
  styleUrls: ['./results-view.component.less']
})
export class ResultsViewComponent implements OnInit {

  constructor() {
    this.resultsTextFilter= "";

  }
  resultsTextFilter:string;

  ngOnInit(): void {
  }

}
