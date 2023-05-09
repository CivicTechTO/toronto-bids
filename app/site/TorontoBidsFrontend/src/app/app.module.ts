import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';

import { AppComponent } from './app.component';
import { BrowserAnimationsModule } from '@angular/platform-browser/animations';
import { SearchComponentComponent } from './search-component/search-component.component';
import { FormsModule,ReactiveFormsModule } from '@angular/forms';
import { MatLegacySelectModule as MatSelectModule} from '@angular/material/legacy-select';
import { MatLegacyInputModule as MatInputModule } from '@angular/material/legacy-input';
import { MatIconModule } from '@angular/material/icon'
import {MatSidenavModule} from '@angular/material/sidenav';


import { MatDatepickerModule} from '@angular/material/datepicker';
import { MatNativeDateModule } from '@angular/material/core';
import { MatLegacyCard as MatCard, MatLegacyCardModule as MatCardModule} from '@angular/material/legacy-card';
import { MatLegacyFormFieldModule as MatFormFieldModule } from "@angular/material/legacy-form-field";
import { SearchFiltersComponent } from './search-filters/search-filters.component';
import { ResultsViewComponent } from './results-view/results-view.component';
import { NgSelectModule } from '@ng-select/ng-select';

import { HttpClientModule } from '@angular/common/http';
@NgModule({
  declarations: [
    AppComponent,
    SearchComponentComponent,
    SearchFiltersComponent,
    ResultsViewComponent
  ],
  imports: [
    BrowserModule,
    BrowserAnimationsModule,
    MatSelectModule,
    MatDatepickerModule,
    MatCardModule,
    MatNativeDateModule,
    MatFormFieldModule,
    FormsModule,
    ReactiveFormsModule,
    MatInputModule,
    MatIconModule,
    HttpClientModule,
    MatSidenavModule,
    NgSelectModule
  ],
  providers: [],
  bootstrap: [AppComponent]
})
export class AppModule { }
