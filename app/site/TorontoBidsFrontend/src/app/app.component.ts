import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { JsonPipe } from '@angular/common';
import { Commodity, CommodityType, SearchQuery } from './models/models';
import { MatNativeDateModule } from '@angular/material/core';
import { MatDatepicker } from '@angular/material/datepicker';
import { MatLegacyCard as MatCard } from '@angular/material/legacy-card';
import { Observable } from 'rxjs';
@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.less'],
})
/*
  TODO
  - Render all the search pieces
  - Observable for
    - Division
    - Type
    - Commodity
    - Commodity Type
    - Buyer

  - Create service

  -




*/

export class AppComponent {
  title = 'TorontoBidsFrontend';
  divisions = ['Division A', 'Division B', 'Division C'];
  commodities = [{value:CommodityType.Any,display:'Any'},{value:CommodityType.ConstructionServices,display:'Construction Services'}, {value:CommodityType.GoodsAndServices,display:'Goods and Services'}, {value:CommodityType.ProfessionalServices,display:'Professional Services'}];
  types = ['Type A', 'Type B', 'Type C'];
  buyer = '';

  searchQuery : SearchQuery;
  commodityType: CommodityType;

	constructor() {
    this.commodityType = CommodityType.Any;
    this.searchQuery = {
      postingStartDate : null,
      postingEndDate : null,
      closingStartDate : null,
      closingEndDate : null,
      buyer : '',
      commodityType : CommodityType.Any,
      commodity:Commodity.Any,
      type:'',
      division:''
    }
	}
}
