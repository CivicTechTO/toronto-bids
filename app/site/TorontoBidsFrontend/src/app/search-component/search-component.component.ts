import { Component, OnInit } from '@angular/core';
import { FormGroup, FormControl } from '@angular/forms';
import { ApiServiceService } from '../api-service.service';
import { faCalendar } from '@fortawesome/free-regular-svg-icons';
import { Commodity, CommoditySubType, Division, OfferType } from '../models/models';
const today = new Date();
const month = today.getMonth();
const year = today.getFullYear();
@Component({
  selector: 'app-search-component',
  templateUrl: './search-component.component.html',
  styleUrls: ['./search-component.component.less']
})
export class SearchComponentComponent implements OnInit {

  offerTypes: OfferType[] = [];
  selectedType: number = 14;

  divisions: Division[] = [];
  selectedDivision: number = 13;


  faCalendar = faCalendar;

  commodities: Commodity[] = []
  selectedCommodity: number = 4;

  commoditySubTypes: Map<number, CommoditySubType[]>;
  selectedCommoditySubType: number = 0;

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


  constructor(private apiService: ApiServiceService) {
    this.commodities = apiService.getCommodities();
    this.divisions = apiService.getDivisions();
    this.offerTypes = apiService.getOfferTypes();
    this.commoditySubTypes = new Map();

    const commodityTypes = apiService.getCommodityTypes();
    for (let i = 0; i < commodityTypes.length; i++) {
      if (!this.commoditySubTypes.get(commodityTypes[i].commodity_id)) {
        this.commoditySubTypes.set(i, []);
      }
      this.commoditySubTypes.get(i)?.push({ id: commodityTypes[i].id, commodity_id: commodityTypes[i].commodity_id, commodity_type: commodityTypes[i].commodity_type });
    }
    console.log(this.commodities);
    console.log(commodityTypes);
  }

  ngOnInit(): void {
    // this.commodities$ = this.apiService.getCommodities();
  }

}
