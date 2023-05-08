import { Component, OnInit } from '@angular/core';
import {FormGroup, FormControl} from '@angular/forms';
import { ApiServiceService } from '../api-service.service';
import { Observable } from 'rxjs';
const today = new Date();
const month = today.getMonth();
const year = today.getFullYear();
export interface Commodity {
  id : string;
  name : string;
}
@Component({
  selector: 'app-search-component',
  templateUrl: './search-component.component.html',
  styleUrls: ['./search-component.component.less']
})
export class SearchComponentComponent implements OnInit {
  commodities$! : Observable<Commodity[]>;

  selectedCommodity : string = "";

  // commoditySubTypes : Map<string,string[]> = new Map([
  //   ['Health Care', ['Health Care A', 'HC B', 'HC C']],
  //   ['Something Else', ['SE 1','SE 2']],
  // ]);
  // selectedSubCommodity : string = "";

  // divisions : string[] = ['']
  // selectedDivision : string = "";
  
  postingDate = new FormGroup({
    postingStart: new FormControl<Date | null>(null),
    postingEnd: new FormControl<Date | null>(null),
  });

  closingDate = new FormGroup({
    closingStart: new FormControl<Date | null>(null),
    closingEnd: new FormControl<Date | null>(null),
  });


  constructor(private apiService:ApiServiceService) {
   
  }

  ngOnInit(): void {
    this.commodities$ = this.apiService.getCommodities();
  }

}
