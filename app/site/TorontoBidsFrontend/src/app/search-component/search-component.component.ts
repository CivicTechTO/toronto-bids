import { Component, OnInit } from '@angular/core';
import {FormGroup, FormControl} from '@angular/forms';
const today = new Date();
const month = today.getMonth();
const year = today.getFullYear();
@Component({
  selector: 'app-search-component',
  templateUrl: './search-component.component.html',
  styleUrls: ['./search-component.component.less']
})
export class SearchComponentComponent implements OnInit {
  commodities : string[] = ['Health Care','Something Else'];
  commoditySelected : FormControl = new FormControl();

  commoditySubTypes : Map<string,string[]> = new Map([
    ['Health Care', ['Health Care A', 'HC B', 'HC C']],
    ['Something Else', ['SE 1','SE 2']],
  ]);
  commoditySubTypesSelected : FormControl = new FormControl();

  postingDate = new FormGroup({
    postingStart: new FormControl<Date | null>(null),
    postingEnd: new FormControl<Date | null>(null),
  });

  closingDate = new FormGroup({
    closingStart: new FormControl<Date | null>(null),
    closingEnd: new FormControl<Date | null>(null),
  });


  constructor() { }

  ngOnInit(): void {
  }

}
